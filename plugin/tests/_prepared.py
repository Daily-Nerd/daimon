"""Test fixtures over `view` and `briefing.prepare` for a checkpoint held in
memory (#1132 PR 7b).

`in_hand` is `briefing.prepare` for a checkpoint a test already has, with the
snapshot read from `route`'s bucket. `synthetic` builds a snapshot from plain
data (no store), and `shown` runs a checkpoint through the view and the
stamps the way `prepare` does. Both call the real functions: nothing here
decides what is withheld or stamped."""

import dataclasses
import uuid
from types import MappingProxyType

from daimon_briefing import briefing, normalize, view

NOW = 1_800_000_000.0


def in_hand(checkpoint, route, now=NOW, **kw):
    """`briefing.prepare` over `checkpoint` instead of the stored latest. The
    snapshot is `route`'s; `live` is True, as for a briefing."""
    snap = view.snapshot(route)
    return briefing.prepare(route, now, opened=view._opened(checkpoint, snap,
                                                            True), **kw)


def qkeys(*texts, kind="decision"):
    """The `(kind, value_key)` pairs a quarantine of these texts holds."""
    return {(kind, normalize.content_key(t)) for t in texts}


def synthetic(resolutions=None, *, amendments=None, quarantine=None,
              forgotten=None, closed=False):
    """A snapshot of plain data: nothing is read from a store."""
    return dataclasses.replace(
        view.Snapshot.empty(),
        resolutions=MappingProxyType(dict(resolutions or {})),
        amendments=MappingProxyType(dict(amendments or {})),
        quarantined=frozenset(quarantine or ()),
        forgotten=frozenset(forgotten or ()), closed=closed)


def shown(checkpoint, snap, now=NOW, *, live=True):
    """`(checkpoint, opened, candidates)`: the checkpoint through the view
    (`live` drops closed loops), then `briefing.stamp`. The input is never
    touched; `opened.withheld` names what the view removed and
    `opened.suppressed` counts the closed loops."""
    opened = view._opened(checkpoint, snap, live)
    out, candidates, _stale = briefing.stamp(opened.checkpoint, snap, now,
                                             with_stale=False)
    return out, opened, candidates


class FrozenClock:
    """A clock stuck on one instant, for a test that needs two writes inside
    the same second. Ids stay random: only the time is pinned."""

    def __init__(self, ns):
        self._ns = int(ns)

    def now_ns(self):
        return self._ns

    def new_id(self):
        return uuid.uuid4().hex


def own_read(project, *, own=True):
    """The read decision of a surface whose own project is `project`: what
    `view.model_read` answers for it under the current settings."""
    from daimon_briefing import config
    return view.ModelRead(view.read_scope(project), project, own=own,
                          cross=config.model_cross_project())


def annotated_of(checkpoint, *, handoff=None, project_dir=None,
                 worldcheck_project=None, snap=None, notes=(), now=NOW,
                 drift=()):
    """The `Annotated` of a checkpoint in hand, for a test that renders it:
    the furniture comes from the real builder (`briefing._furniture`), with
    `worldcheck_project` as the gate (the project whose panels are read, None
    for none) and `project_dir` the project the rulings are read for."""
    return briefing.Annotated(
        checkpoint, (), {}, [], None, [], None, snap, notes=tuple(notes),
        handoff=handoff,
        furniture=briefing._furniture(project_dir, worldcheck_project, snap,
                                      notes, now),
        version_note=(briefing.version_note(checkpoint)
                      if isinstance(checkpoint, dict) else ""),
        drift=tuple(drift or ()))


def rendered(checkpoint, project_dir=None, worldcheck_project=None, *,
             loops_pointer=True, snap=None, notes=(), handoff=None, now=NOW):
    """`briefing.render` for a checkpoint in hand, with the panels of
    `worldcheck_project` (the gate of `briefing._furniture`, the one builder)
    when a test asks for them."""
    furniture = (briefing._furniture(project_dir, worldcheck_project, snap,
                                     notes, now)
                 if project_dir is not None or worldcheck_project is not None
                 else briefing.Furniture())
    return briefing.render(checkpoint, project_dir, loops_pointer=loops_pointer,
                           snap=snap, notes=notes, handoff=handoff,
                           furniture=furniture)


def render_brief(checkpoint, drift=None, teammates=None, handoff=None,
                 project_dir=None, worldcheck_project=None, trailer=None,
                 loops_pointer=True, snap=None, notes=(), lead=()):
    """`render.render_brief` for a checkpoint in hand: the same print, from
    an `Annotated` built the way `prepare` builds it (`annotated_of`)."""
    from daimon_briefing import render
    return render.render_brief(
        annotated_of(checkpoint, handoff=handoff, project_dir=project_dir,
                     worldcheck_project=worldcheck_project, snap=snap,
                     notes=notes, drift=drift),
        teammates=teammates, trailer=trailer,
        loops_pointer=loops_pointer, lead=lead)
