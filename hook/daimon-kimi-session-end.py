#!/usr/bin/env python3
"""Kimi Code SessionEnd hook: checkpoint the session that just closed.

Measured on Kimi Code 0.42.0 (2026-09-09): this fires on an interactive exit
with `reason: "exit"`, and NEVER on `kimi -p` print mode (measured twice; a
print session ends with "To resume this session" and stays resumable, so it
never closes). `Stop` is what captures print mode, so an interactive session
that ran turns and then exited reaches this hook with a Stop capture already
behind it. This hook is deliberately NOT throttled against that capture, the
same call Codex makes: Stop's throttle window is the interval between the last
spawned Stop and `/exit`, so honouring its marker here would drop every turn
inside that window, the session's tail, which is the part an end-of-session
checkpoint exists to keep. The repeat is cheap instead: `daimon serialize`
compares the transcript's sha against the last checkpoint before any LLM work,
so bytes Stop already captured cost one file read, not a second model call.

Unlike every host adapted before it, the payload carries NO transcript path
and no environment variable holds one. The path is resolved from the session
id against Kimi's own storage (lib.kimi_transcript), so "no transcript
resolved" is a routine outcome worth a legible log line rather than silence.

Set DAIMON_KIMI_SERIALIZE_ON_SESSION_END=0 to disable. The LLM work runs
detached via `daimon serialize`, so the hook returns immediately.
Diagnostics land in ~/.daimon/logs/serialize.log.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared helpers live in a same-dir sibling module (see _daimon_hook_lib.py).
# A stale/partial install may lack it: fail open with a logged one-liner rather
# than crash. _fallback_log below mirrors lib.log for exactly that window.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

STATE_DIR = Path.home() / ".daimon" / "kimi"
TAG = "kimi-session-end"


def _fallback_log(line: str) -> None:
    """Best-effort serialize.log write when the shared lib is unavailable
    (stale/partial install). Mirrors lib.log so a broken install still leaves a
    breadcrumb instead of a crash. Never raises."""
    try:
        log_dir = Path.home() / ".daimon" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _enabled() -> bool:
    if lib.disabled():
        return False
    val = os.environ.get("DAIMON_KIMI_SERIALIZE_ON_SESSION_END", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def main() -> int:
    if lib is None:
        _fallback_log(f"{TAG}: hook library missing (_daimon_hook_lib.py) - skipped")
        return 0
    if not _enabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        lib.log(f"{TAG}: unparseable stdin payload - skipped")
        return 0
    if not isinstance(payload, dict):
        lib.log(f"{TAG}: stdin payload was not an object - skipped")
        return 0

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        lib.log(f"{TAG}: payload carried no session_id - skipped")
        return 0

    transcript_path = lib.kimi_transcript(session_id)
    if transcript_path is None:
        # Routine on this host, not a corruption: the payload has no path, so
        # a lookup that finds nothing means the storage moved, KIMI_CODE_HOME
        # differs from the session's, or the id is a shape the resolver
        # refuses. Naming the id and the root is what makes it diagnosable.
        lib.log(f"{TAG}: transcript not found for {session_id} "
                f"(searched {lib.kimi_home()}/sessions) - skipped")
        return 0

    cli = lib.resolve_cli()
    if cli is None:
        lib.log(f"{TAG}: `daimon` CLI not found - checkpoint skipped")
        return 0

    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd, "kimi")
    try:
        # `--session` is not optional here: every Kimi transcript is named
        # `wire.jsonl`, so without it the CLI would derive the id "wire" for
        # every session on the machine (#988).
        if lib.spawn_serialize(cli, str(transcript_path), child_env,
                               session_id=session_id) is False:
            # `is False` (not falsy) on purpose — see scar 0062: the suite's
            # spawn fakes return None, and a truthiness check would flip every
            # one of them into this skip branch.
            lib.log(f"{TAG}: skipped serialize for {session_id} "
                    f"(already in flight) (transcript: {transcript_path})")
            return 0
        lib.log(f"{TAG}: spawned serialize for {session_id} "
                f"(project: {cwd or '?'}) (transcript: {transcript_path})")
    except OSError as exc:
        lib.log(f"{TAG}: spawn failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        if lib is not None:
            lib.log(f"{TAG}: hook error ({type(exc).__name__}: {exc})")
        else:
            _fallback_log(f"{TAG}: hook error ({type(exc).__name__}: {exc})")
        sys.exit(0)
