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


# #923: a Claude Code session that is continued into a NEW session id (a
# background continuation from a slash command, a bridge pickup) leaves the
# host's pointer near the tail of the PARENT transcript, and SessionEnd then
# reports the parent, not the child where the work happened. Bounded tail read:
# transcripts run to tens of MB, the pointer sits in the last few lines.
_CONTINUED_TAIL_BYTES = 64 * 1024
_CONTINUED_MAX_HOPS = 8


def _continued_into(transcript: Path) -> Path | None:
    """The transcript a `continued-in` pointer in `transcript`'s tail names, or
    None when there is no pointer in the tail window. Never raises."""
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as fh:
            fh.seek(max(0, size - _CONTINUED_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            if '"continued-in"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "continued-in":
                continue
            target = str(entry.get("continuedInSessionId") or "").strip()
            if target and Path(target).name == target:
                return transcript.parent / f"{target}.jsonl"
            return None
    except OSError:
        return None
    return None


def _follow_continuation(transcript_path: str) -> tuple[str, list[str]]:
    """Walk `continued-in` pointers from the reported transcript to the last
    transcript that exists on disk. Returns (path to serialize, hop log lines).
    A pointer to a missing file, a cycle, or the hop cap ends the walk at the
    last transcript that resolved."""
    current = Path(transcript_path)
    seen = {current.resolve()}
    hops: list[str] = []
    for _ in range(_CONTINUED_MAX_HOPS):
        nxt = _continued_into(current)
        if nxt is None or not nxt.exists():
            break
        key = nxt.resolve()
        if key in seen:
            break
        hops.append(f"session-end: continued-in {current.stem} -> {nxt.stem}")
        seen.add(key)
        current = nxt
    return str(current), hops


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
    # #923: serialize the transcript the work is in, not the parent stub the
    # host named. The spawn line below then carries the child's stem and path,
    # which is what the ledger pairs a result line with (#28).
    transcript_path, hops = _follow_continuation(transcript_path)
    if hops:
        for hop in hops:
            lib.log(hop)
        session_id = Path(transcript_path).stem
    # Per-project routing: hand the session's working directory to the child so
    # the serializer writes this project's latest pointer (plus the global one).
    # No cwd in the payload -> child env untouched -> pre-routing behavior.
    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd, "claude-code")
    try:
        if lib.spawn_serialize(cli, transcript_path, child_env) is False:
        # #813: `is False` (not falsy) on purpose — only an explicit skip
        # takes this branch, so any caller or fake still returning None
        # keeps the old spawn-and-log path instead of silently flipping.
        # The log line has to be honest: the ledger parses "spawned
        # serialize", and a spawn with no result reads as hung and
        # invites `heal` to retry work that never started.
            lib.log(
                f"session-end: skipped serialize for {session_id} "
                f"(already in flight) (transcript: {transcript_path})")
            return 0
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
