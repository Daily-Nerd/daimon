"""Test fixtures over `view` and `briefing.prepare` for a checkpoint held in
memory (#1132 PR 7b).

`in_hand` is `briefing.prepare` for a checkpoint a test already has, with the
snapshot read from `route`'s bucket. `synthetic` builds a snapshot from plain
data (no store), and `shown` runs a checkpoint through the view and the
stamps the way `prepare` does. Both call the real functions: nothing here
decides what is withheld or stamped."""

import dataclasses
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
