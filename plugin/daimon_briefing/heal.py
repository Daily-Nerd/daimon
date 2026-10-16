"""The heal plan core and the machine-wide heal lock (#1179, design D7, D11, D12).

One notion of "healable" for `status`, the single `daimon heal` and the serial
`daimon heal --all` runner: `plan_all` classifies serialize.log with the same
`ledger._compute_outstanding` and answers every healable target in scope,
newest first, plus the failures it cannot repair, each with a stable code.
`ledger._heal_plan` keeps its dict and is written over this plan.

This module is pure planning plus one lock. It never runs a serialize: the
loop that does lives in `cli/serialize.py`, because it reaches the serialize
runner and the retry marker through the cli namespace (tests replace both there)
and a ledger-layer module may not import an entry module.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import NamedTuple

from . import config, ledger

# Annotated before the import, like jsonl's: every use honors the None guard.
_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX: the lock degrades to a no-op
    _fcntl = None

LOCK_NAME = ".heal.lock"

_NEWER_TOOK_THE_RUN = "newer failure took this run — re-run 'daimon heal' to reach it"


class HealTarget(NamedTuple):
    """One session `heal` can repair. The first six fields are the host-facing
    shape (section 3.2 of the design); `project`, `line` and `age_str` are what
    the runner needs to start the serialize and word its output, and a host
    facing type is free to drop them."""

    session_id: str
    project_slug: str | None
    transcript: str
    kind: str          # "error" | "hung"
    age: int | None
    retried: bool
    project: str | None = None   # the spawn line's cwd, where the checkpoint is routed
    line: str | None = None      # the failed result line, the retry marker's `prior`
    age_str: str = ""


class HealSkip(NamedTuple):
    session_id: str
    reason: str   # "retry-exhausted" | "unrecoverable" | "hung" | "admission-refused" | "repairing"
    detail: str   # the human wording `daimon heal` prints
    age_str: str = ""


class HealPlan(NamedTuple):
    targets: tuple   # tuple[HealTarget, ...], newest first, every healable in scope
    skipped: tuple   # tuple[HealSkip, ...]
    notes: tuple
    order: tuple = ()  # every in-scope session id in classifier order (the legacy dict's skip order)


def _skip_detail(f: dict) -> str:
    if f["class"] == "admission-refused":
        return f"admission refused: events.jsonl is {f['state']}; {f['hint']}"
    if f["class"] == "repairing":
        hb = f.get("heartbeat_age")
        return ("repair already running "
                + (f"(heartbeat {int(hb)}s ago)" if hb is not None else "(starting)"))
    return ledger._HEAL_SKIP_REASON.get(f["class"], "not auto-repairable")


def _presumed_dead_predicate(slug, before, now):
    """The classifier's `presumed_dead(sid, entry)` for one scoped project, or
    None when the host asserted nothing (D11).

    A session is presumed dead only when ALL of these hold: it belongs to the
    scoped project (its stamp's content equals the slug; an unattributed stamp
    or no stamp falls back to the spawn cwd), its latest spawn line is older
    than `before`, and no stamp was touched at or after `before`. A session
    attributable to neither is never presumed dead. An unreadable heartbeat dir
    proves no life, so it presumes nothing alive."""
    if not slug or before is None:
        return None
    index: dict | None = None

    def dead(sid, entry) -> bool:
        nonlocal index
        spawn_ts = entry.get("spawn_ts")
        if spawn_ts is None or spawn_ts >= before:
            return False
        if index is None:
            try:
                index = ledger.heartbeat_index(now)
            except OSError:
                index = {}
        beat = index.get(ledger._session_key(sid))
        if beat is not None and now - beat.age >= before:
            return False
        owner = (beat.project_slug if beat is not None and beat.project_slug
                 else ledger._slug_of(entry.get("project")))
        return owner == slug
    return dead


def plan_all(text: str, now: float, *, force: bool = False,
             slug: str | None = None, sessions: Iterable[str] | None = None,
             attempted: frozenset | set = frozenset(),
             presumed_dead_before: float | None = None) -> HealPlan:
    """Every healable target in scope, newest first. Pure: `now` is injected.

    Scope: `slug` keeps the failures whose spawn cwd is that project, `sessions`
    keeps the named ones (each id through the canonical `_session_key`, so a
    Codex rollout stem names its bare-id session), both narrow together, and
    neither is machine-wide. A failure out of scope is neither a target nor a
    skip. `attempted` is the runner's set for this run: those sessions are
    gone from the plan, which is what stops `force` (it ignores the retry
    marker) from healing one session forever. `presumed_dead_before` needs a
    `slug` and is ignored without one.

    Every healable item the classifier yields (`kind` error or hung) carries
    `kind`, `age` and `retried`; they are read as required keys so a producer
    that drops one fails here instead of defaulting to a wrong answer."""
    presumed = _presumed_dead_predicate(slug, presumed_dead_before, now)
    if presumed is None:
        outstanding = ledger._compute_outstanding(text, now, force=force)
    else:
        outstanding = ledger._scan_outstanding(
            text, now, force=force, presumed_dead=presumed)
    wanted = ({ledger._session_key(s) for s in sessions}
              if sessions is not None else None)
    kept = [f for f in outstanding
            if f["sid"] not in attempted
            and (wanted is None or f["sid"] in wanted)
            and (not slug or ledger._slug_of(f["project"]) == slug)]
    targets = tuple(
        HealTarget(f["sid"], ledger._slug_of(f["project"]) or None,
                   f["transcript"], f["kind"], f["age"], f["retried"],
                   f["project"], f["line"], f["age_str"])
        for f in kept if f["class"] == "healable")
    skipped = tuple(
        HealSkip(f["sid"], f["class"], _skip_detail(f), f["age_str"])
        for f in kept if f["class"] != "healable")
    return HealPlan(targets, skipped, (), tuple(f["sid"] for f in kept))


@contextmanager
def try_lock() -> Iterator[bool]:
    """The machine-wide heal lock (D12): one non-blocking flock on
    `<log dir>/.heal.lock`. Yields True when this process may heal and False
    when another heal holds it, so the caller exits 0 instead of piling a second
    runner onto the same backend. Released on every exit, including a raise,
    and by the OS if the holder dies.

    Fail-open like `jsonl.dir_lock`: no fcntl (non-POSIX), an unwritable log
    dir or any lock error other than contention yields True, which is exactly
    the pre-lock behavior (the per-session `repairing` guard still stops one
    session healing twice). The sidecar is opened with the builtin `open`,
    never `Path.open`: it carries no content."""
    if _fcntl is None:
        yield True
        return
    fh = None
    held = True
    try:
        d = config.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        fh = open(d / LOCK_NAME, "a+")
        try:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError:
            held = False
    except OSError:
        pass
    try:
        yield held
    finally:
        if fh is not None:
            try:
                if held:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                fh.close()
            except OSError:
                pass
