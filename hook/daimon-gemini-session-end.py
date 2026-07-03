#!/usr/bin/env python3
"""Gemini CLI SessionEnd hook: serialize the ending session into a checkpoint.

Mirrors the Claude Code SessionEnd hook: reads the payload from stdin
({session_id, transcript_path, cwd, reason, ...}) and spawns
`daimon serialize <transcript_path>` as a DETACHED background process, so
the exiting CLI is never blocked on an LLM call (Gemini treats SessionEnd as
best-effort and will not wait anyway).

KNOWN UPSTREAM LIMITATION (gemini-cli#14715, current as of 2026-07-01):
Gemini CLI sends transcript_path as an EMPTY stub — the field exists in the
payload but carries no path. Until upstream lands it, this hook's PRIMARY
behavior is the graceful skip: exit 0 with a logged skip line. The spawn path
below is ready for the day the field is populated. For the same reason there
is deliberately NO Gemini branch in the transcript parser yet — no real
transcript format exists to parse.

Fail-open: always exit 0, never print to stdout (Gemini requires pure-JSON
stdout; silence is the only other legal output). Diagnostics go to the
serialize log.
"""

import json
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


def main() -> int:
    if lib is None:
        _fallback_log("gemini-session-end: hook library missing (_daimon_hook_lib.py) - skipped")
        return 0
    if lib.disabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        lib.log("gemini-session-end: unparseable stdin payload - skipped")
        return 0

    transcript_path = payload.get("transcript_path", "")
    if not str(transcript_path).strip():
        # The expected case today: upstream sends an empty stub (gemini-cli#14715).
        lib.log(
            "gemini-session-end: transcript_path empty "
            "(upstream stub, gemini-cli#14715) - skipped"
        )
        return 0
    if not Path(transcript_path).exists():
        lib.log(f"gemini-session-end: transcript not found ({transcript_path!r}) - skipped")
        return 0

    cli = lib.resolve_cli()
    if cli is None:
        lib.log("gemini-session-end: `daimon` CLI not found - checkpoint skipped")
        return 0

    reason = payload.get("reason", "?")
    session_id = payload.get("session_id", "?")
    # Per-project routing: hand the session's working directory to the child so
    # the serializer writes this project's latest pointer (plus the global one).
    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd)
    try:
        lib.spawn_serialize(cli, transcript_path, child_env)
        lib.log(
            f"gemini-session-end: spawned serialize for {session_id} "
            f"(reason: {reason}, project: {cwd or '?'})"
        )
    except OSError as exc:
        lib.log(f"gemini-session-end: spawn failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail-open, but leave a trace
        if lib is not None:
            lib.log(f"gemini-session-end: hook error ({type(exc).__name__}: {exc})")
        else:
            _fallback_log(f"gemini-session-end: hook error ({type(exc).__name__}: {exc})")
        sys.exit(0)
