"""The host heal verbs behind `api.heal_plan` and `api.heal_all` (#1179 PR 4).

`heal_plan` is a pure read over `heal.plan_all` (the one notion of "healable"
that `status` and `daimon heal --all` share). `heal_all` plans, looks at the
machine-wide lock, then starts the detached `daimon heal --all` child through
`heal.launch`, or waits and folds the outcomes through `serialize_liveness`.
It lives beside `serialize_liveness` rather than in `heal.py` because it words
notes through `display` and reads liveness rows, which a ledger-layer module
may not import. It makes no writer call itself: the spawn and the lock stay in
`heal.py`. The contract (statuses, scope, `presumed_dead_before`) is documented
on `daimon_briefing.api`.
"""

import sys
import time

from . import config, display, heal, serialize_liveness
from .heal import HealPlan, HealReport
from .store import project_slug

# How much of serialize.log a heal read covers: the admission pass's window (D5).
_HEAL_WINDOW_BYTES = serialize_liveness._ADMISSION_WINDOW_BYTES


def _heal_scope(project_dir, sessions, everywhere, presumed_dead_before):
    """`(project, slug, notes)` for a heal read. `slug` is None when the scope
    is machine-wide (`everywhere`) or only `sessions` narrows it, and a heal
    that names neither is the caller's job to refuse: an empty slug must never
    widen a plan to the whole machine."""
    notes: list = []
    project = slug = None
    if not everywhere:
        project = config.resolve_project_dir(project_dir)
        slug = project_slug(project) or None
    if presumed_dead_before is not None and slug is None:
        notes.append(display.heal_epoch_ignored_note())
    return project, slug, notes


def heal_plan(project_dir=None, *, sessions=None, force=False,
              everywhere=False, presumed_dead_before=None, now=None) -> HealPlan:
    """The sessions `heal_all` would repair, newest first, and the failures it
    cannot (each `HealSkip.reason` a stable code). Pure: it never writes or
    reaps (D4), and `now` defaults to one `time.time()` call. Reads the recent
    window of serialize.log (4 MB, by seek); an unreadable log becomes a note.
    See the module docstring for scope and `presumed_dead_before`."""
    t = now if now is not None else time.time()
    sessions = None if sessions is None else tuple(sessions)
    _, slug, notes = _heal_scope(project_dir, sessions, everywhere,
                                 presumed_dead_before)
    if not everywhere and slug is None and sessions is None:
        return HealPlan((), (), (*notes, display.heal_no_project_note()))
    try:
        text, cut = serialize_liveness.read_log_tail(_HEAL_WINDOW_BYTES)
    except OSError:
        return HealPlan((), (), (*notes, display.serialize_log_unreadable_note()))
    if cut:
        notes.append(display.heal_window_note())
    core = heal.plan_all(text, t, force=force, slug=slug, sessions=sessions,
                         presumed_dead_before=presumed_dead_before)
    return HealPlan(core.targets, core.skipped, (*notes, *core.notes), core.order)


def heal_all(project_dir=None, *, sessions=None, force=False, everywhere=False,
             presumed_dead_before=None, wait=False) -> HealReport:
    """Plan, then start `daimon heal --all` detached and return at once
    (`wait=True` blocks until the child ends and reads `outcomes`). Never
    raises: a child that cannot start is `launch-failed` with a note. Another
    heal holding the machine-wide lock is `already-running`. See the module
    docstring for the statuses and for `presumed_dead_before`."""
    sessions = None if sessions is None else tuple(sessions)
    plan = heal_plan(project_dir, sessions=sessions, force=force,
                     everywhere=everywhere,
                     presumed_dead_before=presumed_dead_before)
    notes = list(plan.notes)
    if not plan.targets:
        return HealReport("nothing-to-heal", (), plan.skipped, (), tuple(notes))
    with heal.try_lock() as free:
        busy = not free
    if busy:        # released again at once: the child takes the lock itself
        return HealReport("already-running", plan.targets, plan.skipped, (),
                          tuple(notes))
    project, slug, _ = _heal_scope(project_dir, sessions, everywhere,
                                   presumed_dead_before)
    argv = [sys.executable, "-m", "daimon_briefing.cli", "heal", "--all"]
    if slug is not None:
        argv += ["--project", str(project)]
    for sid in sessions or ():
        argv += ["--session", sid]
    if force:
        argv.append("--force")
    if presumed_dead_before is not None and slug is not None:
        argv += ["--presumed-dead-before", repr(float(presumed_dead_before))]
    try:
        code = heal.launch(argv, heal.child_env(), wait=wait)
    except Exception as exc:    # the contract: a spawn failure is a report
        notes.append(display.heal_launch_failed_note(type(exc).__name__))
        return HealReport("launch-failed", plan.targets, plan.skipped, (),
                          tuple(notes))
    if not wait:
        return HealReport("started", plan.targets, plan.skipped, (),
                          tuple(notes))
    if code:
        notes.append(display.heal_child_exit_note(code))
    rows = serialize_liveness.serializes(
        project_dir, sessions=[tg.session_id for tg in plan.targets])
    return HealReport("completed", plan.targets, plan.skipped, rows.rows,
                      (*notes, *rows.notes))
