#!/usr/bin/env python3
"""Kimi Code SessionEnd hook: checkpoint the session that just closed.

Measured on Kimi Code 0.42.0 (2026-09-09): this fires on an interactive exit
with `reason: "exit"`, and NEVER on `kimi -p` print mode (measured twice; a
print session ends with "To resume this session" and stays resumable, so it
never closes). `Stop` is what captures print mode, which makes double capture
a real shape here rather than a theoretical one: a session captured by Stop
and then closed interactively arrives at this hook already checkpointed. It
shares the Stop hook's marker for exactly that reason, and the CLI's own
identical-bytes guard is the second line of defence.

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
import time
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


def _safe_name(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _marker_path(session_id: str) -> Path:
    return STATE_DIR / f"{_safe_name(session_id)}.last-stop"


def _already_captured(session_id: str) -> bool:
    """True when the Stop hook already serialized this session inside its
    throttle window.

    On Codex this hook is deliberately unthrottled, because SessionEnd there is
    the real end and Stop is only insurance. Here Stop is a genuine capture
    path for print mode, so an interactive session that ran turns and then
    exited reaches this hook seconds after a Stop already spawned. Repeating it
    would burn a second full LLM call on the same bytes.
    """
    interval = _interval_seconds()
    if interval <= 0:
        return False
    try:
        marker = _marker_path(session_id)
        return (marker.exists()
                and time.time() - marker.stat().st_mtime < interval)
    except OSError:
        return False


def _interval_seconds() -> int:
    """Shared with the Stop hook so the two agree on what "just captured"
    means. Reading it here rather than hard-coding a window is what keeps a
    person who set the interval to 0 (serialize every turn) from also
    silencing this hook."""
    raw = os.environ.get("DAIMON_KIMI_MIN_SERIALIZE_INTERVAL", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _mark_spawned(session_id: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(session_id).write_text(str(int(time.time())),
                                            encoding="utf-8")
    except OSError:
        pass


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

    if _already_captured(session_id):
        lib.log(f"{TAG}: skipped serialize for {session_id} "
                f"(already captured by the Stop hook)")
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
        _mark_spawned(session_id)
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
