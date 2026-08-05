#!/usr/bin/env python3
"""Codex SessionEnd hook: checkpoint the true end of a session.

`Stop` fires at turn scope and is throttled, so the final tail of a session is
whatever the throttle happened to allow rather than the real end state. This
hook is the event the Stop hook has been apologising for not having: it fires
once, on graceful teardown, with the complete transcript.

It does NOT replace the Stop hook. `run_session_end_hooks` is the last statement
in graceful teardown, so a crash, a SIGKILL or a closed terminal window means it
never runs. Stop stays as crash insurance for exactly that case.

Deliberately unthrottled: this is the real end, not a heartbeat. It still writes
the Stop hook's marker, so a Stop arriving moments earlier or later does not
serialize the same transcript twice.

Set DAIMON_CODEX_SERIALIZE_ON_SESSION_END=0 to disable. The LLM work runs
detached via `daimon serialize <transcript_path>`, so the hook returns
immediately against Codex's 3 second cap. Diagnostics land in
~/.daimon/logs/serialize.log.
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

STATE_DIR = Path.home() / ".daimon" / "codex"
TAG = "codex-session-end"


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
    val = os.environ.get("DAIMON_CODEX_SERIALIZE_ON_SESSION_END", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _safe_name(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _mark_spawned(session_id: str) -> None:
    """Write the marker the Stop hook throttles against.

    Unconditional, unlike the Stop hook's own version, which skips the write
    when the interval is 0. The point here is not throttling this hook but
    suppressing a redundant Stop for the same transcript, and that suppression
    has to hold regardless of how the interval is configured."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = STATE_DIR / f"{_safe_name(session_id)}.last-stop"
        marker.write_text(str(int(time.time())), encoding="utf-8")
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

    transcript_path = payload.get("transcript_path", "")
    if not transcript_path or not Path(transcript_path).exists():
        lib.log(f"{TAG}: transcript not found ({transcript_path!r}) - skipped")
        return 0

    session_id = str(payload.get("session_id") or Path(transcript_path).stem)

    cli = lib.resolve_cli()
    if cli is None:
        lib.log(f"{TAG}: `daimon` CLI not found - checkpoint skipped")
        return 0

    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd)
    try:
        lib.spawn_serialize(cli, transcript_path, child_env)
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
