#!/usr/bin/env python3
"""Windsurf Cascade adapter: serialize from the native transcript when
Cascade provides one, accumulate a daimon-side transcript otherwise (#35, #70).

Register this ONE script for THREE Cascade hook events (docs:
https://docs.windsurf.com/windsurf/cascade/hooks):

    pre_user_prompt                        -> records the user side of the
                                               turn (legacy accumulation path)
    post_cascade_response                   -> records the assistant side +
                                               (throttled) spawns
                                               `daimon serialize` on the
                                               ACCUMULATED transcript
    post_cascade_response_with_transcript   -> (throttled) spawns
                                               `daimon serialize` directly on
                                               Cascade's NATIVE transcript —
                                               preferred when available; no
                                               accumulation write for this
                                               event

Native transcript preferred: issue #70 field evidence showed Windsurf DOES
write a native `.jsonl` transcript (~/.windsurf/transcripts/<trajectory_id>.jsonl)
when `post_cascade_response_with_transcript` is registered, carrying both
conversation sides. When that path is registered and its file exists, it is
the source of truth and accumulation becomes redundant for that trajectory.

Why accumulation still exists (legacy path, #35): the plain
`post_cascade_response` payload carries NO transcript path, and hosts that
only register the two legacy events never get one. Windsurf otherwise keeps
its conversations out of reach, so daimon keeps its own: each hook call
appends a `**role**:`-marked turn to
~/.daimon/windsurf/transcripts/<trajectory_id>.md — the exact markdown shape
`daimon serialize` already parses.

Self-probing (#62): any payload this adapter cannot handle — unextractable
`pre_user_prompt` text, a missing trajectory_id, a
`post_cascade_response_with_transcript` whose transcript_path is missing or
does not exist, or an event it does not know — is dumped to
~/.daimon/windsurf/unparsed-<event>-<stamp>.json so the next adapter
iteration can be built from evidence instead of another probe round. At most
ONE dump per event name: a script registered for all 12 Cascade events must
not flood the state dir.

Throttle: `post_cascade_response` and `post_cascade_response_with_transcript`
both fire EVERY turn; serializing each one is an LLM call per turn.
DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL seconds (default 300, 0 = every turn)
gate the spawn per trajectory — the codex-stop pattern. Both events share the
SAME per-trajectory marker, so registering both never double-spawns.
Accumulation itself is never throttled.

Debounced finalizer (#42): Windsurf has no session-end event, so a session
whose last turns land inside the throttle window above would never be
serialized again — the tail is lost. Every serialize-capable post event
therefore (a) touches a per-trajectory activity stamp and (b) arms a detached
one-shot sleeper (this script re-executed with `--finalize`) carrying the
stamp mtime it just wrote. The sleeper waits
DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS (default 600, fractional accepted,
0 disables arming), then re-reads the stamp: changed (or gone) means a later
turn owns finality and it exits silently; unchanged means this WAS the last
turn, so it spawns the final serialize. Last-writer-wins by stamp equality —
deliberately no lockfile (check-then-write claims race); if two sleepers read
equal stamps and both fire, the second serialize hits the identical-bytes
guard and skips.

Fail-open everywhere: always exit 0; a broken daimon must never break
Cascade. Kill switch: DAIMON_DISABLE=1.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

STATE_DIR = Path.home() / ".daimon" / "windsurf"
TRANSCRIPT_DIR = STATE_DIR / "transcripts"

# Keys tried, in order, for the user-prompt text — the pre_user_prompt payload
# shape is not yet field-confirmed (post_cascade_response is), so the
# extractor is tolerant and everything else lands in an unparsed-*.json dump.
_PROMPT_KEYS = ("prompt", "user_prompt", "text", "message", "content", "input")


def _fallback_log(line: str) -> None:
    try:
        log_dir = Path.home() / ".daimon" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _interval_seconds() -> int:
    raw = os.environ.get("DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _finalizer_quiet_seconds() -> float:
    """Quiet period before the debounced finalizer fires (#42). Fractional
    values are accepted (tests exercise sub-second windows); 0 disables the
    finalizer entirely."""
    raw = os.environ.get("DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS", "600").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 600.0


def _safe_name(trajectory_id: str) -> str:
    return trajectory_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _activity_stamp(trajectory_id: str) -> Path:
    return STATE_DIR / f"{_safe_name(trajectory_id)}.last-activity"


def _touch_activity(trajectory_id: str) -> None:
    """Refresh the per-trajectory activity stamp (#42). EVERY trajectory event
    counts as activity — including a user prompt: while the assistant is still
    generating, a sleeper armed by the previous response must find a changed
    stamp at wake, or it would serialize a mid-generation transcript (the
    accumulation file already holds the new user turn, so the identical-bytes
    guard would not skip that half-state). Inert when the finalizer is
    disabled; fail-open on any write error."""
    if _finalizer_quiet_seconds() <= 0:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _activity_stamp(trajectory_id).write_text(
            str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def _should_spawn(trajectory_id: str) -> bool:
    interval = _interval_seconds()
    if interval <= 0:
        return True
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = STATE_DIR / f"{_safe_name(trajectory_id)}.last-serialize"
        if marker.exists() and time.time() - marker.stat().st_mtime < interval:
            return False
        return True
    except OSError:
        return True


def _mark_spawned(trajectory_id: str) -> None:
    if _interval_seconds() <= 0:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{_safe_name(trajectory_id)}.last-serialize").write_text(
            str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def _append_turn(trajectory_id: str, role: str, text: str) -> Path:
    """Append one `**role**:`-marked turn — the markdown shape
    transcript.from_file's role regex parses (marker starts a turn, following
    lines are its continuation). Turn text is secret-scrubbed at this write
    site (#109): the accumulation file is a disk artifact `daimon serialize`
    reads later, so a quoted secret must not land here raw. main() has already
    gated on lib.redaction_available(), so the scrub is guaranteed real."""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"{_safe_name(trajectory_id)}.md"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"**{role}**: {lib.redact_text(text).strip()}\n\n")
    return path


def _extract_prompt(payload: dict) -> str | None:
    """Tolerant text extraction for the not-yet-confirmed pre_user_prompt
    shape: try tool_info first (where post_cascade_response keeps its data),
    then the payload root."""
    sources = []
    tool_info = payload.get("tool_info")
    if isinstance(tool_info, dict):
        sources.append(tool_info)
    sources.append(payload)
    for src in sources:
        for key in _PROMPT_KEYS:
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return None


def _redact_payload(obj):
    """Deep copy of `obj` with every string leaf secret-scrubbed (#109). Keys,
    structure, and non-string scalars are preserved so the probe dump stays a
    faithful, usable diagnostic — only secret-shaped substrings inside string
    values are masked. main() has gated on lib.redaction_available().

    JSON payloads only ever yield dict/list/str/scalar, but the helper is
    fail-safe against reuse: tuples are scrubbed like lists, and ANY other
    unrecognized type is rendered via str() and scrubbed rather than passed
    through raw — a silent skip would contradict the disk-wide guarantee."""
    if isinstance(obj, str):
        return lib.redact_text(obj)
    if isinstance(obj, dict):
        return {k: _redact_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_payload(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return lib.redact_text(str(obj))


def _dump_probe(event: str, payload: dict) -> bool:
    """Bounded self-probe dump (#62): at most one unparsed-*.json per event
    name. Returns True when a dump was written (callers log only then, so a
    hook registered for every Cascade event stays quiet after the first).
    Payload string leaves are secret-scrubbed at this write site (#109) so no
    quoted secret reaches unparsed-*.json, while structure stays usable."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tag = _safe_name(event or "unknown")
        if any(STATE_DIR.glob(f"unparsed-{tag}-*.json")):
            return False
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (STATE_DIR / f"unparsed-{tag}-{stamp}.json").write_text(
            json.dumps(_redact_payload(payload), indent=2, ensure_ascii=False),
            encoding="utf-8")
        return True
    except OSError:
        return False


def _project_cwd() -> str:
    """Best-effort project dir: the hook process cwd, unless it is somewhere
    meaningless (home, root). The payload carries no workspace path."""
    cwd = os.getcwd()
    if cwd in (str(Path.home()), "/", ""):
        return ""
    return cwd


def main() -> int:
    if lib is None:
        _fallback_log("windsurf-cascade: hook library missing (_daimon_hook_lib.py) — skipped")
        return 0
    if lib.disabled():
        return 0
    if not lib.redaction_available():
        # #109: every write site below (accumulation, probe dumps) persists
        # transcript-derived text. Without the redaction module we cannot
        # guarantee a quoted secret is scrubbed, so skip rather than write raw —
        # #104's disk guarantee outranks accumulation. Fail-open: still exit 0.
        lib.log("windsurf-cascade: redaction module unavailable — skipped "
                "(no raw transcript persisted)")
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        lib.log("windsurf-cascade: unparseable stdin payload — skipped")
        return 0
    if not isinstance(payload, dict):
        lib.log("windsurf-cascade: non-object payload — skipped")
        return 0

    event = str(payload.get("agent_action_name") or "")
    trajectory_id = str(payload.get("trajectory_id") or "").strip()
    if not trajectory_id:
        if _dump_probe(f"{event or 'unknown'}-no-trajectory-id", payload):
            lib.log(f"windsurf-cascade: no trajectory_id on {event or '?'} — "
                    "payload dumped, skipped")
        return 0

    if event == "pre_user_prompt":
        # A prompt is trajectory activity: touch the stamp so a sleeper armed
        # by the PREVIOUS response defuses instead of firing while the
        # assistant is still generating. Deliberately no arming here — if the
        # session dies between prompt and response, the lone unanswered
        # prompt isn't worth a finalizer; the response's post event arms.
        _touch_activity(trajectory_id)
        text = _extract_prompt(payload)
        if text is None:
            if _dump_probe(event, payload):
                lib.log("windsurf-cascade: pre_user_prompt shape unknown — dumped "
                        "for the next adapter iteration (transcript stays "
                        "assistant-only)")
            return 0
        _append_turn(trajectory_id, "user", text)
        return 0

    if event == "post_cascade_response_with_transcript":
        tool_info = payload.get("tool_info")
        raw_path = tool_info.get("transcript_path") if isinstance(tool_info, dict) else None
        native_path = Path(raw_path) if isinstance(raw_path, str) and raw_path.strip() else None
        if native_path is None or not native_path.exists():
            if _dump_probe(event, payload):
                lib.log("windsurf-cascade: post_cascade_response_with_transcript "
                        "transcript_path missing or absent — dumped for the next "
                        "adapter iteration")
            return 0
        _spawn_serialize_for(trajectory_id, native_path,
                             f"native transcript: {native_path}")
        _arm_finalizer(trajectory_id, native_path)
        return 0

    if event != "post_cascade_response":
        # Unknown events dump instead of vanishing (#62).
        if _dump_probe(event, payload):
            lib.log(f"windsurf-cascade: unhandled event {event} — payload dumped "
                    "for the next adapter iteration")
        return 0

    tool_info = payload.get("tool_info")
    response = tool_info.get("response") if isinstance(tool_info, dict) else None
    if not (isinstance(response, str) and response.strip()):
        lib.log(f"windsurf-cascade: empty response for {trajectory_id} — skipped")
        return 0
    transcript_path = _append_turn(trajectory_id, "assistant", response)

    _spawn_serialize_for(trajectory_id, transcript_path,
                         f"transcript: {transcript_path}")
    _arm_finalizer(trajectory_id, transcript_path)
    return 0


def _spawn_serialize_for(trajectory_id: str, transcript_path, detail: str) -> None:
    """Throttled spawn of `daimon serialize` shared by BOTH post events — same
    per-trajectory marker, so registering post_cascade_response AND
    post_cascade_response_with_transcript together never double-spawns (#70)."""
    if not _should_spawn(trajectory_id):
        lib.log(f"windsurf-cascade: skipped serialize for {trajectory_id} (throttled)")
        return
    cli = lib.resolve_cli()
    if cli is None:
        lib.log("windsurf-cascade: `daimon` CLI not found — checkpoint skipped")
        return
    cwd = _project_cwd()
    try:
        lib.spawn_serialize(cli, str(transcript_path), lib.project_env(cwd))
        _mark_spawned(trajectory_id)
        lib.log(f"windsurf-cascade: spawned serialize for {trajectory_id} "
                f"(project: {cwd or '?'}) ({detail})")
    except OSError as exc:
        lib.log(f"windsurf-cascade: spawn failed ({type(exc).__name__}: {exc})")


def _arm_finalizer(trajectory_id: str, transcript_path) -> None:
    """Touch the per-trajectory activity stamp and arm one detached finalizer
    sleeper for it (#42). Called on EVERY serialize-capable post event — one
    sleeper per event is fine and bounded by the quiet window; the stamp
    comparison at wake makes all but the last turn's sleeper exit silently.
    Fail-open: an arming failure must never break Cascade."""
    if _finalizer_quiet_seconds() <= 0:
        return
    _touch_activity(trajectory_id)
    try:
        armed_mtime_ns = _activity_stamp(trajectory_id).stat().st_mtime_ns
    except OSError:
        return  # touch failed or stamp unreadable — nothing safe to arm on
    # Same detached shape as lib.spawn_serialize: stderr to the crash log so
    # an uncaught sleeper traceback is preserved without polluting
    # serialize.log; start_new_session so it survives the exiting hook.
    try:
        log_dir = Path.home() / ".daimon" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "serialize-crash.log").open("a", encoding="utf-8") as crashf:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--finalize",
                 trajectory_id, str(transcript_path), str(armed_mtime_ns)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=crashf,
                start_new_session=True,  # survive the exiting parent
                env=os.environ.copy(),
            )
    except OSError:
        pass


def _finalize(trajectory_id: str, transcript_path: str, armed_mtime_ns: str) -> int:
    """Finalizer-sleeper mode (#42): sleep the quiet period, then serialize the
    session tail ONLY if this sleeper's arming turn is still the trajectory's
    last activity. Ownership is decided by stamp mtime equality, never a
    lockfile — a lockfile's check-then-write would just move the race, while
    a duplicate fire is harmless (the serialize child's identical-bytes guard
    skips it). Transcript contents are never parsed here — only the path,
    captured at arm time, is passed through."""
    if lib is None or lib.disabled():
        return 0
    try:
        armed_ns = int(armed_mtime_ns)
    except ValueError:
        return 0  # malformed arm argument — nothing safe to own
    time.sleep(_finalizer_quiet_seconds())
    try:
        current_ns = _activity_stamp(trajectory_id).stat().st_mtime_ns
    except OSError:
        return 0  # stamp gone — nothing left to finalize
    if current_ns != armed_ns:
        return 0  # a later turn refreshed activity; its sleeper owns finality
    cli = lib.resolve_cli()
    if cli is None:
        lib.log("windsurf-finalizer: `daimon` CLI not found — final flush skipped")
        return 0
    cwd = _project_cwd()
    try:
        lib.spawn_serialize(cli, transcript_path, lib.project_env(cwd))
        # Logged at FIRE time, not arm time: an arm-time spawn line would sit
        # unresolved for the whole quiet period and read as hung to the ledger.
        lib.log(f"windsurf-finalizer: spawned serialize for {trajectory_id} "
                f"(project: {cwd or '?'}) (transcript: {transcript_path})")
    except OSError as exc:
        lib.log(f"windsurf-finalizer: spawn failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 5 and sys.argv[1] == "--finalize":
            sys.exit(_finalize(sys.argv[2], sys.argv[3], sys.argv[4]))
        sys.exit(main())
    except Exception as exc:  # fail-open, but leave a trace
        if lib is not None:
            lib.log(f"windsurf-cascade: hook error ({type(exc).__name__}: {exc})")
        else:
            _fallback_log(f"windsurf-cascade: hook error ({type(exc).__name__}: {exc})")
        sys.exit(0)
