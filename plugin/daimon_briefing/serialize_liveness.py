"""Serialize liveness for hosts (#1179 PR 2): `api.serializes`.

A read-only join of the heartbeat stamps, the serialize.log fold and the
checkpoint directory into one row per session. It lives beside `ledger`
rather than in it because `api` may import no writer module and `ledger`
holds the writers; everything here only reads. The folds and the classifier
it reuses (`_session_ledger`, `_outstanding_failures`) stay in `ledger`.
"""

import time
from pathlib import Path
from typing import NamedTuple

from . import config, display, store
from .ledger import (_ADMISSION_WINDOW_BYTES, _memoized_admission_state,
                     _outstanding_failures, _session_key, _session_ledger,
                     _slug_of, _checkpoint_stamp, heartbeat_index)


def read_log_tail(window: int = _ADMISSION_WINDOW_BYTES) -> tuple[str, bool]:
    """`(text, cut)`: the last `window` bytes of serialize.log from a line
    start, and whether the log was longer than that (D5). Unlike the full
    read the heal pass and `admission_notes` do before slicing in memory, this
    opens the file, seeks and reads only the window, so the I/O is bounded on
    a long-lived machine. A missing log is no activity (`("", False)`); any
    other failure to read it raises OSError, for the caller to report. A cut
    inside a multibyte character is dropped with the partial first line."""
    try:
        with (config.log_dir() / "serialize.log").open("rb") as f:
            size = f.seek(0, 2)
            start = max(0, size - window)
            if start:
                # One byte before the window tells whether it starts on a line.
                f.seek(start - 1)
            else:
                f.seek(0)
            data = f.read()
    except FileNotFoundError:
        return "", False
    if start:
        if data[:1] == b"\n":
            data = data[1:]
        else:
            cut_at = data.find(b"\n")
            data = data[cut_at + 1:] if cut_at >= 0 else b""
    return data.decode("utf-8", errors="replace"), start > 0


SERIALIZE_STATES = ("starting", "running", "repairing",
                    "succeeded", "skipped", "failed", "held", "hung",
                    "unknown")
LIVE_STATES = frozenset({"starting", "running", "repairing"})
# What a project-wide listing (`sessions=None`) keeps: a serialize that is
# running, or one that ended and still needs somebody.
_OUTSTANDING_STATES = LIVE_STATES | {"failed", "held", "hung"}


class SerializeRow(NamedTuple):
    """One session's liveness. `state` is one of SERIALIZE_STATES and `reason`
    the stable code behind it; a host pins codes, not prose."""

    session_id: str            # canonical key (D6)
    project_slug: str | None   # stamp content, else slug of the spawn cwd
    state: str
    reason: str | None
    heartbeat_age: float | None
    spawn_age: int | None
    retried: bool


class Serializes(NamedTuple):
    rows: tuple                # tuple[SerializeRow, ...]
    notes: tuple               # warning-marked strings


def serialize_rows_core(text, now, *, slug=None, sessions=None, since=None,
                        heartbeats=None, cut=False, log_unreadable=False,
                        heartbeats_unreadable=False, ceiling=None,
                        transcript_exists=None, admission_state=None,
                        checkpoint_of=None) -> Serializes:
    """The state table of a host's serialize liveness (design 3.1), over a log
    window and a heartbeat index the caller already read.

    `text` is the folded log window (`cut`: the log was longer), `heartbeats`
    the `heartbeat_index` (canonical key -> Heartbeat), `sessions` the ids a
    host asked about (any name a session goes by; one row per requested id,
    in the order given, duplicates included, each carrying the canonical key)
    or None for this project's live and
    outstanding sessions, newest first. `slug` scopes that listing.

    First match wins:
      1. fresh stamp, fold says retried      repairing  heartbeat
      2. fresh stamp                         running    heartbeat
      3. latest result success               succeeded  result
      4. latest result skip                  skipped    result
      5. refused admission still held        held       admission-refused
      6. latest result error                 failed     healable | retry-exhausted | unrecoverable
      7. spawn, no result, within ceiling    starting (repairing if retried)  spawned
      8. spawn, no result, past ceiling      hung       healable | hung
      9. no line, checkpoint created >= since  succeeded  checkpoint
     10. no line, a checkpoint but no proof    unknown    checkpoint-without-run
     11. nothing                               unknown    not-found | outside-window | log-unreadable

    The checkpoint (rows 9-10) is read only for a session with no stamp and
    no log line, and counts only against `since`: a checkpoint of an EARLIER
    run of a resumed session must not read as this run's result. The injected
    readers default to the live ones; with all of them given this is pure."""
    ceiling = config.hung_after_seconds() if ceiling is None else ceiling
    transcript_exists = transcript_exists or (lambda p: bool(p) and Path(p).exists())
    admission_state = admission_state or _memoized_admission_state()
    checkpoint_of = checkpoint_of or _checkpoint_stamp
    heartbeats = heartbeats or {}
    fold = _session_ledger(text, now, tail=None) if text else {}
    slugs: dict = {}

    def project_of(key, hb, entry):
        if hb is not None and hb.project_slug:
            return hb.project_slug
        project = entry["project"] if entry else None
        if project not in slugs:
            slugs[project] = _slug_of(project) or None
        return slugs[project]

    def classify(key, entry, hb_age):
        """The classifier's verdict on one entry, checkpoints ignored."""
        found = _outstanding_failures(
            {key: entry}, now, lambda sid: False, ceiling, transcript_exists,
            heartbeat_age=lambda sid: hb_age, admission_state=admission_state)
        return found[0] if found else None

    def row_for(key):
        hb = heartbeats.get(key)
        entry = fold.get(key)
        hb_age = hb.age if hb is not None else None
        fresh = hb_age is not None and hb_age <= ceiling
        retried = bool(entry and entry["retried"])
        spawn_age = entry["spawn_age"] if entry else None
        project = project_of(key, hb, entry)
        held = None

        def make(state, reason):
            return SerializeRow(key, project, state, reason, hb_age,
                                spawn_age, retried)

        if fresh:
            return make("repairing" if retried else "running", "heartbeat"), held
        if entry is not None:
            kind = entry["result_kind"]
            if kind == "success":
                return make("succeeded", "result"), held
            if kind == "skipped":
                return make("skipped", "result"), held
            if kind == "error":
                got = classify(key, entry, hb_age)
                cls = got["class"] if got else "unrecoverable"
                if cls == "admission-refused":
                    return make("held", "admission-refused"), got
                return make("failed", cls), held
            if entry["spawned"] and spawn_age is not None:
                if spawn_age <= ceiling:
                    return make("repairing" if retried else "starting",
                                "spawned"), held
                got = classify(key, entry, hb_age)
                return make("hung", got["class"] if got else "hung"), held
        exists, created = checkpoint_of(key)
        if exists:
            if since is not None and created is not None and created >= since:
                return make("succeeded", "checkpoint"), held
            return make("unknown", "checkpoint-without-run"), held
        if log_unreadable:
            return make("unknown", "log-unreadable"), held
        return make("unknown", "outside-window" if cut else "not-found"), held

    if sessions is not None:
        keys = [_session_key(str(s)) for s in sessions]
    else:
        keys = list(dict.fromkeys([*fold, *heartbeats]))

    rows = []
    held_items = []
    for key in keys:
        row, held = row_for(key)
        if sessions is None and (not slug or row.project_slug != slug
                                 or row.state not in _OUTSTANDING_STATES):
            continue
        rows.append(row)
        if held is not None and slug and row.project_slug == slug:
            held_items.append(held)
    if sessions is None:
        def age_of(r):
            return r.spawn_age if r.spawn_age is not None else r.heartbeat_age
        rows.sort(key=lambda r: (age_of(r) is None, age_of(r) or 0))

    notes = []
    if log_unreadable:
        notes.append(display.serialize_log_unreadable_note())
    if heartbeats_unreadable:
        notes.append(display.heartbeats_unreadable_note())
    if any(r.reason == "outside-window" for r in rows):
        notes.append(display.serialize_outside_window_note())
    if held_items:
        notes.append(display.admission_refused_note(
            len(held_items), held_items[0]["state"], held_items[0]["hint"]))
    return Serializes(tuple(rows), tuple(notes))


def serializes(project_dir=None, *, sessions=None, since=None,
               now=None) -> Serializes:
    """What a host that spawns serializes may ask: is each one still running,
    how did it end, did its checkpoint land (design 3.1, the table is on
    `serialize_rows_core`).

    `project_dir` names the project the listing is about, resolved like every
    other read (#948); `None` names none, as in `requests.join`, so only an
    explicit `sessions` list answers.

    `sessions=[...]` returns exactly one row per requested id, in the order
    given, regardless of project (two names for one session give two equal
    rows); any name a session goes by works
    (D6: the bare id or a Codex `rollout-...` stem), and the row carries the
    canonical key. `sessions=None` returns this project's live and
    outstanding sessions, newest first.

    A drain waits until no row is in `LIVE_STATES`. `unknown` before the
    first line is "not started yet" until the host's own deadline, never
    "gone": a host that spawns `daimon serialize` itself has no spawn line,
    and the child stamps only after its preflight.

    `since` (epoch) is for the sessions that leave no log line: a checkpoint
    counts as this run's result only when its `created` stamp (the time of the
    transcript's last row, not the spawn) is at or after `since`. Pass the
    time you recorded when the session you wait on started or resumed. Too
    early a `since` lets a stale checkpoint of an earlier run read as
    `succeeded`; without one a checkpoint alone reads `unknown`
    (`checkpoint-without-run`). A successful in-process (Hermes) capture
    writes no log line, so it is `succeeded` only through `since`.

    Only the last 4 MB of serialize.log is read, by seek. A requested session
    outside that window reads `unknown` (`outside-window`) with a note.
    Reads never write or reap (D4); `now` defaults to one `time.time()` call.
    Never raises on an unreadable log or heartbeat directory: those become
    notes."""
    t = now if now is not None else time.time()
    slug = store.project_slug(config.resolve_project_dir(project_dir)) or ""
    log_unreadable = hb_unreadable = cut = False
    text = ""
    try:
        text, cut = read_log_tail()
    except OSError:
        log_unreadable = True
    beats: dict = {}
    try:
        beats = heartbeat_index(t)
    except OSError:
        hb_unreadable = True
    return serialize_rows_core(
        text, t, slug=slug, sessions=sessions, since=since, heartbeats=beats,
        cut=cut, log_unreadable=log_unreadable,
        heartbeats_unreadable=hb_unreadable)


def serialize_rows(project_dir=None, *, sessions=None, since=None,
                   now=None) -> tuple:
    """`serializes(...).rows`, for a caller with no use for the notes."""
    return serializes(project_dir, sessions=sessions, since=since, now=now).rows
