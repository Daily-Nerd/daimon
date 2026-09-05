"""The serialize.log ledger: append result/retry lines, parse them back, and
classify per-session outcomes.

Extracted verbatim from cli.py (#147) — this is the single subsystem behind
`daimon status` and `daimon heal`: writers (_append_serialize_log,
_append_retry_log), the line-format regexes, the last-of-each-kind tail view
(_parse_serialize_log), the per-session fold (_session_ledger), the lost-session
classifier (_outstanding_failures / _compute_outstanding), and the heal decision
(_heal_plan). Every regex here is a load-bearing contract with the lines the
hooks and _run_serialize write; change them together or not at all.

The stats fold over the same log lives here too (#162, second pure-move
slice): the every-line tally (_stats_capture) and the in-window spawn probe
(_spawns_in_window) with its stamp parser (_parse_stamp), so the
"new prefix -> update the parser" rule has a single home.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, store, transcript


def _append_serialize_log(line: str) -> None:
    """Append a result line to serialize.log so manual/CLI serializes are
    visible to `status`, not only hook-spawned ones (FR #27). Best-effort:
    logging must never break a serialize."""
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _append_retry_log(session_id: str, prior: str) -> None:
    """Mark a #26 heal retry in serialize.log BEFORE re-serializing. The line is
    a TIMESTAMPED spawn-style marker (matching the hook spawn-line stamp format)
    so `status` surfaces it AND the dedup check can find it later — one retry per
    session, ever, BY DEFAULT. That cap deliberately survived the cache-buster
    era (#15): retries used to be pointless byte-identical replays against a
    caching gateway, so capping at one was strictly correct; the serializer now
    cache-busts both failure layers, so a second heal is no longer guaranteed
    to reproduce the first failure. The cap stays anyway — it bounds token burn
    on a permanently-bad transcript — but `daimon heal --force` is the explicit
    operator override past it (`_outstanding_failures`'s `force` param). This
    writer's format is unchanged either way: a forced retry appends the SAME
    marker shape, so a forced heal re-classifies as retry-exhausted again until
    the next --force. Best-effort: never break a heal."""
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} session-start: retry serialize for {session_id} (prior: {prior})\n")
    except OSError:
        pass


# Hook spawn line: `<iso-stamp> <hook>: spawned serialize for <id> (...)`,
# where <hook> is `session-end` (Claude), `codex-stop` (Codex), or
# `gemini-session-end` (Gemini — must be listed BEFORE a bare `session-end`
# would substring-match it; the alternation is exact so order only matters for
# readability). The #26 heal retry marker (`<iso> session-start: retry
# serialize for <id> (...)`) is also a spawn for status purposes, so both the
# host and the verb are alternations. A new host adapter MUST add its prefix
# here or its serializes are invisible to status/hung detection/heal.
_SPAWN_RE = re.compile(
    r"^(\S+) (?:gemini-session-end|codex-session-end|session-end|codex-stop|"
    r"windsurf-cascade|"
    r"windsurf-finalizer|session-start): "
    r"(?:spawned|retry) serialize for (\S+)"
)
# Child stdout/stderr land in the log RAW (no timestamp): the serialize
# success/error lines printed by _cmd_serialize above.
_RESULT_OK_RE = re.compile(r"^wrote checkpoint: .+ \(took (\d+)s\)")
_RESULT_ERR_RE = re.compile(r"^error: .*?(?: after (\d+)s)?$")


def _format_age(seconds) -> str:
    """Coarse human age: 59 -> '59s', 61 -> '1m', 7200 -> '2h', 432000 -> '5d'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _parse_serialize_log(path, now) -> dict | None:
    """Tail of serialize.log -> {spawn, result}, or None when there's no log.

    Lines from overlapping sessions interleave, so spawn and result are
    reported INDEPENDENTLY (last of each kind) — no pairing is attempted.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    spawn = result = None
    for line in text.splitlines()[-200:]:  # tail is plenty; the log only appends
        line = line.strip()
        m = _SPAWN_RE.match(line)
        if m:
            spawn = {"session_id": m.group(2), "timestamp": m.group(1)}
            continue
        m = _RESULT_OK_RE.match(line)
        if m:
            result = {"outcome": "success", "duration_seconds": int(m.group(1)), "line": line}
            continue
        m = _RESULT_ERR_RE.match(line)
        if m:
            duration = int(m.group(1)) if m.group(1) else None
            result = {"outcome": "error", "duration_seconds": duration, "line": line}
    if spawn:
        try:
            ts = datetime.strptime(spawn["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            age = int(now - ts.replace(tzinfo=timezone.utc).timestamp())
            spawn["age_seconds"] = age
            spawn["age"] = _format_age(age)
        except ValueError:
            pass  # unexpected stamp format: report the spawn without an age
    return {"spawn": spawn, "result": result}


# ---- heal: opportunistic ONE-shot repair of the most recent FAILED serialize ----

# The transcript carried by an error result line (see _run_serialize):
# `error: <exc> (transcript: <path>) after <N>s` for serialize failures, or
# `error: <preflight msg> (transcript: <path>)` for pre-flight errors (#49) —
# the `after Ns` clause is optional so both attribute to their session. A
# pre-flight-failed session with its transcript on disk is healable: fixing
# the config (e.g. adding the API key) makes the retry succeed.
_HEAL_TRANSCRIPT_RE = re.compile(r"\(transcript: (.+?)\)(?: after \d+s|$)")

# Per-session ledger regexes (kept SEPARATE from _RESULT_OK_RE/_RESULT_ERR_RE,
# which _parse_serialize_log depends on). Success lines embed the session id in
# the checkpoint path: `wrote checkpoint: <dir>/<session>.json (took Ns)`.
_LEDGER_OK_RE = re.compile(r"^wrote checkpoint: (.+?) \(took \d+s\)")
_LEDGER_SKIP_RE = re.compile(r"^skipped serialize for (\S+):")
# #925: the serializer's per-capture count of user rows attributed by a
# host-declared speaker line; summed lifetime by _stats_capture.
_SPEAKER_LINE_RE = re.compile(r"speaker line: (\d+) user row\(s\) attributed")
_LEDGER_PROJECT_RE = re.compile(r"project: (.*?)\)")
# #28: hooks stamp the transcript path on the spawn line as a TRAILING group —
# `... (reason: r, project: p) (transcript: <path>)` — so a child that crashes
# before writing any result line still leaves a healable trail. Trailing-only
# match keeps it disjoint from _HEAL_TRANSCRIPT_RE (error lines, `after Ns`).
_LEDGER_SPAWN_TRANSCRIPT_RE = re.compile(r"\(transcript: (.+?)\)\s*$")

# #634: Codex names BOTH its rollout transcript and the checkpoint written from
# it `rollout-<stamp>-<session-id>`, while its hooks spawn-log the BARE session
# id. Attributing a result by file stem therefore never meets its spawn, so
# every successful Codex capture also left a phantom "spawned, no result"
# failure in status. The stamp shape is matched exactly because both halves
# contain dashes — a looser split would be ambiguous. Non-Codex stems (a Claude
# checkpoint stem IS the session id) must pass through untouched.
_ROLLOUT_STEM_RE = re.compile(r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)$")


def _session_key(stem: str) -> str:
    """Checkpoint/transcript file stem -> the session id its spawn line carries.
    Identity for every host except Codex (#634)."""
    m = _ROLLOUT_STEM_RE.match(stem)
    return m.group(1) if m else stem


def _has_checkpoint(sid: str) -> bool:
    """Did this session's serialize land a checkpoint? A direct read covers
    every host whose checkpoint is named by session id; the scan covers Codex,
    whose file is on disk under the rollout name the bare id cannot address
    (#634). The glob pattern is a literal — `sid` never reaches it — so no
    session id can inject shell-style metacharacters into the match.

    The scan needs no OSError guard, unlike this module's other filesystem
    readers: `Path.glob` swallows a missing directory, a path that is a file
    rather than a directory, and a permission-denied directory, returning an
    empty iterator for all three (verified on 3.10 and 3.13, the versions CI
    runs). A `try/except OSError` here would be unreachable."""
    if store.read_checkpoint(sid) is not None:
        return True
    return any(_session_key(p.stem) == sid
               for p in config.checkpoint_dir().glob("rollout-*.json"))


def _session_ledger(text: str, now: float) -> dict:
    """Fold serialize.log into per-session terminal state. Unlike
    _parse_serialize_log (last-of-each-kind, no pairing), this attributes every
    line to its session_id — spawn regex group, success checkpoint-path stem, or
    error transcript stem — so a failure is never masked by a later session's
    success. Pre-flight errors (no transcript) carry no session and are dropped."""
    sessions: dict = {}

    def _entry(sid: str) -> dict:
        return sessions.setdefault(sid, {
            "spawned": False, "spawn_ts": None, "spawn_age": None, "project": None,
            "result_kind": None, "result_line": None, "transcript": None,
            "retried": False,
        })

    for line in text.splitlines()[-200:]:
        line = line.strip()
        m = _SPAWN_RE.match(line)
        if m:
            e = _entry(m.group(2))
            e["spawned"] = True
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ")
                e["spawn_ts"] = ts.replace(tzinfo=timezone.utc).timestamp()
                e["spawn_age"] = int(now - e["spawn_ts"])
            except ValueError:
                pass
            pm = _LEDGER_PROJECT_RE.search(line)
            if pm:
                raw = pm.group(1).strip()
                e["project"] = raw if (raw and raw != "?") else None
            tm = _LEDGER_SPAWN_TRANSCRIPT_RE.search(line)
            if tm:
                e["transcript"] = tm.group(1)
            if "retry serialize for" in line:
                e["retried"] = True
            continue
        m = _LEDGER_OK_RE.match(line)
        if m:
            e = _entry(_session_key(Path(m.group(1)).stem))
            e["result_kind"] = "success"
            e["result_line"] = line
            e["transcript"] = None
            continue
        m = _LEDGER_SKIP_RE.match(line)
        if m:
            e = _entry(m.group(1))
            e["result_kind"] = "skipped"
            e["result_line"] = line
            continue
        if _RESULT_ERR_RE.match(line):
            tm = _HEAL_TRANSCRIPT_RE.search(line)
            if not tm:
                continue  # pre-flight error, no session to attribute
            e = _entry(_session_key(Path(tm.group(1)).stem))
            e["result_kind"] = "error"
            e["result_line"] = line
            e["transcript"] = tm.group(1)
    return sessions


def checkpoint_covers(sid: str, transcript_path) -> bool:
    """Does this session's checkpoint reach the end of its transcript? (#929)

    "Lost" used to mean "no checkpoint at all", so a session that was captured
    once, resumed for hours, and whose re-capture then FAILED read as captured
    on every surface: `heal` dropped the failure because a checkpoint existed,
    `status` printed fresh, and the session-start sweep deferred to heal. This
    is the sweep's own rule applied here: a checkpoint counts only when its
    `created` stamp is at or after the transcript's last conversation row.
    Content, never file mtime: hosts append cost rows after the end hook.

    Ambiguous is not lost (#54): no transcript path, an unreadable transcript,
    a stamp-free transcript, a legacy checkpoint with no `created`, or a
    checkpoint the bare id cannot address (Codex rollout names, #634) all
    answer True, so the fold stays exactly as quiet as before for them."""
    if not transcript_path:
        return True
    cp = store.read_checkpoint(sid)
    if cp is None:
        return True
    created = store._created_epoch(cp.get("created"))
    if created is None:
        return True
    # last_timestamp answers None for an unreadable, non-jsonl, or stamp-free
    # transcript by contract; it never raises, so no guard here.
    last = store._created_epoch(transcript.last_timestamp(transcript_path))
    if last is None:
        return True
    return created >= last


def _outstanding_failures(ledger, now, has_checkpoint, ceiling, transcript_exists,
                          force=False, heartbeat_age=None,
                          checkpoint_covers=None) -> list:
    """Sessions still LOST — no checkpoint AND latest state != success.
    `has_checkpoint(sid)` and `transcript_exists(path)` are injected so this
    stays pure/testable. error+spawn+transcript-on-disk+not-retried -> healable
    (exactly what heal will repair); error but retried -> retry-exhausted; error
    but no spawn record or transcript gone -> unrecoverable (lost, heal can't
    retry it); spawn with no result older than `ceiling` -> hung.

    `force` (#15) is the `daimon heal --force` escape hatch: it ignores the
    `retried` gate on both the error and hung paths, so a retry-exhausted (or
    retried-hung) session reclassifies as `healable` again PROVIDED its
    transcript still exists — force can't repair what's genuinely gone, so
    those stay `unrecoverable`/`hung`. Callers that don't pass `force` (e.g.
    `status`, and default `heal`) see classification exactly as before."""
    out = []
    for sid, e in ledger.items():
        if e["result_kind"] in ("success", "skipped"):
            continue
        # #929: a checkpoint clears the session only when it covers the
        # transcript; callers that inject no `checkpoint_covers` keep the
        # pre-#929 "any checkpoint" reading.
        if has_checkpoint(sid) and (checkpoint_covers is None
                                    or checkpoint_covers(sid, e["transcript"])):
            continue
        age = e["spawn_age"]
        if e["result_kind"] == "error":
            if e["retried"] and not force:
                cls = "retry-exhausted"
            elif e["spawned"] and e["transcript"] and transcript_exists(e["transcript"]):
                cls = "healable"
            else:
                cls = "unrecoverable"
            out.append({"sid": sid, "kind": "error", "class": cls, "age": age,
                        "age_str": _format_age(age) if age is not None else "unknown",
                        "transcript": e["transcript"], "project": e["project"],
                        "spawned": e["spawned"], "line": e["result_line"]})
        elif e["result_kind"] is None and e["spawned"] and age is not None and age > ceiling:
            # #342: liveness beats wall-clock. A heartbeat fresher than the
            # ceiling means the serialize is ALIVE — not outstanding, so
            # status stays quiet and heal cannot fork a retry against a
            # still-running child. Absent heartbeat (pre-#342 host hook, or
            # a child dead before its first touch) falls through to the
            # wall-clock rule unchanged.
            if heartbeat_age is not None:
                hb = heartbeat_age(sid)
                if hb is not None and hb <= ceiling:
                    continue
            # #28: a spawn line that recorded its transcript makes a hung
            # (crashed/killed) serialize healable — the checkpoint is
            # recoverable as long as the transcript is still on disk. The
            # one-retry-ever policy (#26) applies unchanged via `retried`,
            # unless `force` (#15) overrides it.
            t = e["transcript"]
            cls = ("healable"
                   if t and transcript_exists(t) and (not e["retried"] or force)
                   else "hung")
            out.append({"sid": sid, "kind": "hung", "class": cls, "age": age,
                        "age_str": _format_age(age), "transcript": t,
                        "project": e["project"], "spawned": True, "line": None})
    out.sort(key=lambda f: (f["age"] is None, f["age"] or 0))
    return out


def _compute_outstanding(text: str, now: float, force: bool = False) -> list:
    """Wire the pure ledger/classifier to the live store + filesystem. Single
    source for both `status` (display) and `heal` (repair) so their notion of
    'outstanding' can never drift. `force` (#15) is forwarded to
    `_outstanding_failures`; callers that don't pass it get unchanged default
    classification."""
    return _outstanding_failures(
        _session_ledger(text, now), now,
        _has_checkpoint,
        config.hung_after_seconds(),
        lambda p: bool(p) and Path(p).exists(),
        force=force,
        # #342: module-level heartbeat_age resolves via the global, not the
        # classifier's same-named parameter.
        heartbeat_age=lambda sid: heartbeat_age(sid, now),
        checkpoint_covers=checkpoint_covers,
    )


_HEAL_SKIP_REASON = {
    "retry-exhausted": "retry already attempted, still failing (re-run with --force)",
    "unrecoverable": "no spawn record or transcript gone — cannot auto-heal",
    "hung": "spawned, no result (hung/killed) — transcript unavailable",
}


def _heal_plan(text, now, force=False) -> dict:
    """Decide what `heal` will repair and why. Pure — `now` injected. Reuses the
    SAME _compute_outstanding source as status, so their notion of healable agrees.
    target = the newest `healable` (already gauntlet-vetted); every other outstanding
    failure lands in `skipped` with a reason; `note` is the headline when there is no
    target. `force` (#15) is forwarded to _compute_outstanding — the classifier does
    the actual retry-exhausted-to-healable promotion, so this layer needs no
    special-casing beyond passing the flag through."""
    outstanding = _compute_outstanding(text, now, force=force)
    healable = [f for f in outstanding if f["class"] == "healable"]
    target = None
    if healable:
        t = healable[0]  # newest-first
        target = {"sid": t["sid"], "transcript": t["transcript"],
                  "project": t["project"], "age_str": t["age_str"], "line": t["line"]}

    skipped = []
    for f in outstanding:
        if target and f["sid"] == target["sid"]:
            continue
        if f["class"] == "healable":
            reason = "newer failure took this run — re-run 'daimon heal' to reach it"
        else:
            reason = _HEAL_SKIP_REASON.get(f["class"], "not auto-repairable")
        skipped.append({"sid": f["sid"], "age_str": f["age_str"], "reason": reason})

    if target is not None:
        note = ""
    elif not outstanding:
        note = ("nothing to heal — no serialize activity logged"
                if not text.strip() else "nothing to heal — no outstanding failures")
    else:
        n = len(skipped)
        note = f"nothing to heal — {n} failure{'s' if n != 1 else ''} can't be auto-repaired:"
    return {"target": target, "skipped": skipped, "note": note}


# ---- #342: per-session liveness heartbeat ----
#
# Wall-clock hung detection has a cliff: a field install completed a
# serialize at 1732s — 96% of the 1800s ceiling — so a slightly slower but
# alive run would read as hung, and heal would fork a retry while the
# original still worked. Liveness beats wall-clock: the serialize child
# touches <log_dir>/heartbeats/<session_id> at entry and during every
# chunk/pass/merge step; "hung" means no heartbeat for the ceiling window,
# not total duration > ceiling. No heartbeat file at all (crashed child,
# legacy host hook) degrades to exactly the pre-#342 wall-clock rule.

_HEARTBEAT_REAP_SECONDS = 7 * 86400


def _heartbeat_dir() -> Path:
    return config.log_dir() / "heartbeats"


def touch_heartbeat(session_id: str, project_slug: str | None = None) -> None:
    """Stamp liveness for a running serialize (#342). Best-effort: a full
    disk or unwritable dir must never break the serialize doing the
    touching. Old stamps are reaped opportunistically here — result lines
    end a session's classification, so a leftover file is disk hygiene,
    never a liveness signal.

    `project_slug` (#534): the serialize entry point stamps which project
    the run belongs to as the file's CONTENT, so the briefing can answer
    "is a serialize in flight for THIS project" without a session->project
    map. Step touches omit it — Path.touch() updates mtime and preserves
    content, so one stamped entry touch attributes the whole trail. A
    pre-#534 stamp has empty content and stays unattributable on purpose."""
    try:
        d = _heartbeat_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / Path(session_id).name
        if project_slug is not None:
            p.write_text(str(project_slug), encoding="utf-8")
        else:
            p.touch()
        now = time.time()
        for p in d.iterdir():
            try:
                if now - p.stat().st_mtime > _HEARTBEAT_REAP_SECONDS:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass


def clear_heartbeat(session_id: str) -> None:
    """#564: a run's result ends its liveness — the serialize door clears the
    stamp alongside writing its result line, so a completed run can never read
    as in-flight for the rest of the hung ceiling. Best-effort like the touch:
    a leftover stamp (kill -9 before the clear) is still bounded by the
    ceiling, and heal's spawn-no-result classification never needed it."""
    try:
        (_heartbeat_dir() / Path(session_id).name).unlink()
    except OSError:
        pass


def heartbeat_age(session_id: str, now: float | None = None) -> float | None:
    """Seconds since this session's serialize last proved liveness, or None
    when it never has (no stamp — pre-#342 host hook or a child that died
    before its first touch)."""
    try:
        mtime = (_heartbeat_dir() / Path(session_id).name).stat().st_mtime
    except OSError:
        return None
    return max(0.0, (now if now is not None else time.time()) - mtime)


def serialize_in_flight(project_slug: str, now: float | None = None) -> bool:
    """#534: is a serialize LIVE for this project right now? True only for a
    heartbeat whose content matches `project_slug` and whose age is inside
    the hung ceiling — the same liveness bar heal uses, so brief and heal
    never disagree about what "alive" means.

    Every other shape answers False on purpose: a stale stamp is a stuck or
    crashed serialize (heal's case, and a permanent false "one session
    behind" line would be worse than the silence this exists to fix), an
    empty stamp is a pre-#534 trail nothing can attribute, and a missing
    dir is simply no activity."""
    slug = str(project_slug or "").strip()
    if not slug:
        return False
    ceiling = config.hung_after_seconds()
    t = now if now is not None else time.time()
    try:
        entries = list(_heartbeat_dir().iterdir())
    except OSError:
        return False
    for p in entries:
        try:
            if t - p.stat().st_mtime > ceiling:
                continue
            if p.read_text(encoding="utf-8").strip() == slug:
                return True
        except OSError:
            continue
    return False


# Host prefix on a spawn line, for per-host capture counts. Deliberately the
# same alternation as _SPAWN_RE (a new host adapter updates both).
_STATS_HOST_RE = re.compile(
    r"^\S+ (gemini-session-end|codex-session-end|session-end|codex-stop|"
    r"windsurf-cascade|"
    r"windsurf-finalizer): "
    r"spawned serialize for "
)


def _is_result_line(line: str) -> bool:
    """A line the CLI both prints AND logs first-class (_run_serialize's
    print + _append_serialize_log pair) — success, skip, or error. Spawn/host
    lines are hook-written and timestamped, so they are NOT result lines."""
    return bool(_RESULT_OK_RE.match(line) or _LEDGER_SKIP_RE.match(line)
                or _RESULT_ERR_RE.match(line))


# #364: the rolling window behind "is capture healthy NOW" — matches the
# retention window so both instruments describe the same recent past. The gate
# is the reopen/escalate threshold recorded on #364: a rolling error rate above
# it means the foundation the whole product sits on is failing too often.
_CAPTURE_WINDOW_DAYS = 14
_CAPTURE_ERROR_GATE_PCT = 10
# #742: rescue success below this share of windowed attempts reopens that issue.
_RESCUE_GATE_PCT = 50


def _stats_capture(now=None) -> dict:
    """serialize.log -> aggregate counters. Tallies EVERY line (scar #9: no
    last-of-kind collapse — a buried failure still counts), except an
    ADJACENT-identical repeat of a result line (#300).

    #364 adds a `window` sub-dict: the same tallies restricted to the last
    _CAPTURE_WINDOW_DAYS, plus an error-rate percentage over in-window capture
    ATTEMPTS (success + errors; skips are policy outcomes, not attempts).
    Result lines are unstamped, so each inherits the most recent stamped
    line's timestamp; result lines before any stamped line have unknown age
    and count lifetime only (#54 honesty rule: ambiguous, never guessed).
    The window shares this fold's line classification verbatim — dedupe and
    the too-short reclassification below apply identically, so the window can
    never disagree with the lifetime counters about what a line IS.

    #300: every result line is built once, printed to stdout, AND logged
    first-class by _run_serialize. Any spawn path that redirected the child's
    stdout into this log therefore wrote it twice — adjacent, byte-identical,
    untimestamped. The write side is fixed (all spawn_serialize sites set
    stdout=DEVNULL) but historical logs carry the doubles permanently, and this
    fold is what `status` reports, so they are collapsed on READ. Diagnosed
    cosmetic, not double spend: doubled pairs carry exactly one LLM token
    record between them.

    ADJACENCY is the dedupe key, never payload equality: two genuine serializes
    of one session are separated by their own spawn lines, and an immediate
    genuine re-run is short-circuited to a `skipped` line by the
    transcript_unchanged guard (#185/#296) rather than writing a second
    identical success. Scoped to result lines for the same reason — spawn lines
    cannot double via this mechanism, and collapsing two same-second spawns
    would hide a real capture from the #265 silent-capture alarm.

    This is NOT the last-of-kind collapse scar #9 forbids: it never reaches
    across sessions, so a failure buried under a later session's success still
    counts."""
    # Both literals are heterogeneous on purpose: `win` carries counters
    # alongside an `error_rate_pct` that stays None until there is something
    # to divide by, and `out` carries counters alongside a host map and the
    # nested window. Without the annotation the inferred value types are
    # `int | None` and `object`, and every counter increment below fails.
    win: dict = {"days": _CAPTURE_WINDOW_DAYS, "success": 0, "skipped": 0,
                 "errors": 0, "fallback_attempts": 0,
                 "fallback_serializes": 0,
                 "starved": 0, "error_rate_pct": None}
    out: dict = {"success": 0, "skipped": 0, "errors": 0,
                 "fallback_serializes": 0,
                 "fallback_attempts": 0, "starved": 0,
                 "hosts": {}, "max_serialize_seconds": 0,
                 "total_serialize_seconds": 0,
                 "speaker_lines": 0,
                 "window": win}
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        return out
    cutoff = ((now or datetime.now(timezone.utc))
              - timedelta(days=_CAPTURE_WINDOW_DAYS))
    in_window = False  # was the most recent stamped line inside the window?
    prev = None
    for line in text.splitlines():
        line = line.strip()
        doubled = line == prev and _is_result_line(line)
        prev = line
        if doubled:
            continue
        if line:
            stamp = _parse_stamp(line.split()[0])
            if stamp is not None:
                in_window = stamp >= cutoff
        # #341: fallback_serializes counts successes only; the chat() entry
        # warning is the attempt marker. Without it, a fallback that runs and
        # dies is indistinguishable from one that never ran.
        sl = _SPEAKER_LINE_RE.search(line)
        if sl:
            out["speaker_lines"] += int(sl.group(1))  # #925, lifetime only
        if "llm.fallback backend=command" in line:
            out["fallback_attempts"] += 1
            if in_window:
                win["fallback_attempts"] += 1
            continue
        m = _RESULT_OK_RE.match(line)
        if m:
            out["success"] += 1
            took = int(m.group(1))
            out["max_serialize_seconds"] = max(out["max_serialize_seconds"], took)
            out["total_serialize_seconds"] += took
            if in_window:
                win["success"] += 1
            if "[fallback backend]" in line:
                out["fallback_serializes"] += 1
                if in_window:
                    win["fallback_serializes"] += 1
            continue
        if _LEDGER_SKIP_RE.match(line):
            out["skipped"] += 1
            if in_window:
                win["skipped"] += 1
            continue
        if _RESULT_ERR_RE.match(line):
            # #235: too-short is a policy skip, not a failure. The write side
            # has emitted it as a skip line since e2eb989; older logs carry it
            # in error shape, so the fold reclassifies retroactively — `errors`
            # stays "capture should have worked and didn't".
            if "transcript too short" in line:
                out["skipped"] += 1
                if in_window:
                    win["skipped"] += 1
            else:
                out["errors"] += 1
                if in_window:
                    win["errors"] += 1
                # #742: the budget died before the command backend ran a
                # single call — an error still, but its own class: nothing
                # about the backend failed, the shared deadline starved it.
                # #748's chained shape ("rescue failed: ...; primary: LLM
                # deadline exhausted before command backend") carries the
                # substring for a rescue that RAN and failed — never starved.
                if ("deadline exhausted before command backend" in line
                        and "rescue failed:" not in line):
                    out["starved"] += 1
                    if in_window:
                        win["starved"] += 1
            continue
        hm = _STATS_HOST_RE.match(line)
        if hm:
            out["hosts"][hm.group(1)] = out["hosts"].get(hm.group(1), 0) + 1
    attempts = win["success"] + win["errors"]
    if attempts:
        win["error_rate_pct"] = round(win["errors"] * 100 / attempts, 1)
    return out


_USAGE_STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_stamp(token: str):
    try:
        return datetime.strptime(token, _USAGE_STAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# #349: the hosts whose packaged integration runs an auto-briefing at session
# start (and therefore logs `brief:auto`). ONLY these can satisfy the
# stale-hook heuristic — a machine served exclusively by hosts without such a
# hook (Windsurf, Codex today) can never log brief:auto, and warning about it
# is a permanent false positive whose advice cannot fix anything. Add a host
# here the day its integration gains an auto-brief.
AUTO_BRIEF_HOSTS = ("session-end",)


def _spawns_in_window_count(cutoff, hosts=None) -> int:
    """How many hook-spawned captures serialize.log shows inside the window —
    i.e. how many sessions the hooks OBSERVED on this machine since `cutoff`.
    The silent-capture alarm (#265) reads this against checkpoints WRITTEN in the
    same window: spawns without writes is a silent capture failure. Fails open to
    0 when the log is absent. `hosts` (#349) restricts the count to specific
    host prefixes; None counts every host."""
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        line = line.strip()
        m = _STATS_HOST_RE.match(line)
        if not m:
            continue
        if hosts is not None and m.group(1) not in hosts:
            continue
        stamp = _parse_stamp(line.split()[0])
        if stamp is not None and stamp >= cutoff:
            count += 1
    return count


def _spawns_in_window(cutoff, hosts=None) -> bool:
    """True when serialize.log shows any hook-spawned capture inside the
    window — i.e. sessions ARE happening on this machine. The count and the
    boolean share one regex so they can never drift on a new host prefix."""
    return _spawns_in_window_count(cutoff, hosts=hosts) > 0
