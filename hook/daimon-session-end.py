#!/usr/bin/env python3
"""Claude Code SessionEnd hook: serialize the ending session into a checkpoint.

Closes the capture gap: SessionStart injects the latest checkpoint, but until
now nothing WROTE one when a Claude Code session ended — briefings were only as
fresh as the last manual `daimon serialize` run.

Reads the SessionEnd payload from stdin ({session_id, transcript_path, reason,
...}) and spawns `daimon serialize <transcript_path>` as a DETACHED
background process. Serialization is an LLM call (30s+ on long sessions);
blocking /exit on it is unacceptable, so the hook returns immediately and the
child finishes on its own (start_new_session=True survives parent exit).

Fail-open: always exit 0. Diagnostics go to the serialize log, not stdout —
SessionEnd output is never shown to a user anyway, but the log gives the next
session a place to look when a briefing is unexpectedly stale.
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
        _fallback_log("session-end: hook library missing (_daimon_hook_lib.py) — skipped")
        return 0
    if lib.disabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        lib.log("session-end: unparseable stdin payload — skipped")
        return 0

    transcript_path = payload.get("transcript_path", "")
    if not transcript_path or not Path(transcript_path).exists():
        lib.log(f"session-end: transcript not found ({transcript_path!r}) — skipped")
        return 0

    cli = lib.resolve_cli()
    if cli is None:
        lib.log("session-end: `daimon` CLI not found — checkpoint skipped")
        return 0

    reason = payload.get("reason", "?")
    session_id = payload.get("session_id", "?")
    # Per-project routing: hand the session's working directory to the child so
    # the serializer writes this project's latest pointer (plus the global one).
    # No cwd in the payload -> child env untouched -> pre-routing behavior.
    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd)
    try:
        lib.spawn_serialize(cli, transcript_path, child_env)
        # Trailing (transcript: ...) group (#28): if the child crashes before
        # writing any result line, this is the only surviving pointer to the
        # transcript — it makes the hung session healable instead of lost.
        lib.log(
            f"session-end: spawned serialize for {session_id} "
            f"(reason: {reason}, project: {cwd or '?'}) "
            f"(transcript: {transcript_path})"
        )
    except OSError as exc:
        lib.log(f"session-end: spawn failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail-open, but leave a trace
        if lib is not None:
            lib.log(f"session-end: hook error ({type(exc).__name__}: {exc})")
        else:
            _fallback_log(f"session-end: hook error ({type(exc).__name__}: {exc})")
        sys.exit(0)
