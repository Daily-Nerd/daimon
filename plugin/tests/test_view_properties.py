"""Properties of the view over generated checkpoints (#1132 PR 6a).

Hand-rolled and seeded (no hypothesis): each case is a pseudo-random
checkpoint plus a pseudo-random snapshot, checked through `view._opened`, the
projection core every public projection shares.

I2  no value the snapshot withholds survives in an opened checkpoint.
I3  nothing a view hands back besides the checkpoint (the withheld records,
    the notes, their reprs) contains a withheld value.
I5  withholding is monotone: growing the forgotten or quarantined set, or
    closing the snapshot, never makes an item visible.
"""

import dataclasses
import json
import random
from types import MappingProxyType

import pytest

from daimon_briefing import normalize, schema, view

CASES = 300
WORDS = ["gateway", "cache", "token", "retry", "index", "release", "budget",
         "pointer", "ledger", "carry", "recall", "rotate", "migrate", "audit"]


def _text(rng):
    return " ".join(rng.sample(WORDS, 3)) + f" #{rng.randrange(10_000)}"


def _entry(rng, fld):
    entry = {"id": f"o-{rng.randrange(16**6):06x}"}
    for name in schema.VALUE_FIELDS:
        if name == "text" or rng.random() < 0.3:
            entry[name] = _text(rng)
    if fld.key == "contradictions_flagged" and rng.random() < 0.4:
        return _text(rng)                              # a bare string
    return entry


def _checkpoint(rng):
    cp = {"working_context": {}, "epistemic_snapshot": {}}
    for fld in schema.ITEM_FIELDS:
        block = cp[fld.section]
        if fld.singleton:
            if rng.random() < 0.8:
                block[fld.key] = {"text": _text(rng)}
        else:
            block[fld.key] = [_entry(rng, fld) for _ in range(rng.randrange(4))]
    return cp


def _values(cp):
    """[(field, canonical key, raw text)] for every value in the checkpoint."""
    out = []
    for fld, item in schema.iter_items(cp, dicts_only=False):
        entry = item if isinstance(item, dict) else {"text": item}
        for name in schema.VALUE_FIELDS:
            if entry.get(name):
                out.append((fld, normalize.content_key(str(entry[name]).strip()),
                            str(entry[name])))
    return out


def _snapshot(rng, cp):
    values = _values(cp)
    forgotten = frozenset(k for _f, k, _t in values if rng.random() < 0.2)
    quarantined = frozenset((f.kind, k) for f, k, _t in values
                            if rng.random() < 0.2)
    ids = {pair: f"tr-{i:012x}" for i, pair in enumerate(sorted(quarantined))}
    return dataclasses.replace(
        view.Snapshot.empty(), forgotten=forgotten, quarantined=quarantined,
        quarantine_ids=MappingProxyType(ids), closed=rng.random() < 0.1)


def _withheld_values(cp, snap):
    return [t for f, k, t in _values(cp)
            if snap.closed or k in snap.forgotten
            or (f.kind, k) in snap.quarantined]


@pytest.mark.parametrize("seed", range(CASES))
def test_i2_no_withheld_value_survives_in_the_opened_checkpoint(seed):
    rng = random.Random(seed)
    cp = _checkpoint(rng)
    snap = _snapshot(rng, cp)
    opened = view._opened(cp, snap, live_only=False)
    for fld, key, _text in _values(opened.checkpoint):
        assert not snap.closed
        assert key not in snap.forgotten
        assert (fld.kind, key) not in snap.quarantined
    # An item is withheld whole when ANY of its columns is; every other item
    # is kept untouched.
    expected = 0
    for fld, item in schema.iter_items(cp, dicts_only=False):
        entry = item if isinstance(item, dict) else {"text": item}
        hits = [normalize.content_key(str(entry[n]).strip())
                for n in schema.VALUE_FIELDS if entry.get(n)]
        if not (snap.closed or any(
                k in snap.forgotten or (fld.kind, k) in snap.quarantined
                for k in hits)):
            expected += 1
    got = len(list(schema.iter_items(opened.checkpoint, dicts_only=False)))
    assert got == expected


@pytest.mark.parametrize("seed", range(CASES))
def test_i3_nothing_but_the_checkpoint_carries_a_withheld_value(seed):
    rng = random.Random(10_000 + seed)
    cp = _checkpoint(rng)
    snap = _snapshot(rng, cp)
    opened = view._opened(cp, snap, live_only=False)
    blobs = [repr(opened.withheld), "\n".join(snap.notes()),
             json.dumps([dataclasses.asdict(w) for w in opened.withheld]),
             json.dumps(opened.checkpoint)]
    for text in _withheld_values(cp, snap):
        for blob in blobs:
            assert text not in blob, (seed, text)


@pytest.mark.parametrize("seed", range(CASES))
def test_i5_withholding_is_monotone(seed):
    rng = random.Random(20_000 + seed)
    cp = _checkpoint(rng)
    small = _snapshot(rng, cp)
    extra = rng.choice([k for _f, k, _t in _values(cp)] or ["0" * 8])
    bigger = dataclasses.replace(
        small, forgotten=small.forgotten | {extra},
        quarantined=small.quarantined | {(f.kind, k) for f, k, _t in _values(cp)
                                         if rng.random() < 0.3})
    before = {id(i) for _f, i in schema.iter_items(
        view._opened(cp, small, False).checkpoint, dicts_only=False)}
    after = view._opened(cp, bigger, False)
    assert len(list(schema.iter_items(after.checkpoint, dicts_only=False))) <= len(
        before)
    closed = view._opened(cp, dataclasses.replace(bigger, closed=True), False)
    assert list(schema.iter_items(closed.checkpoint, dicts_only=False)) == []


def test_the_generator_exercises_every_branch():
    """Anti-vacuity: across the seeds some cases withhold, some do not, some
    close, and bare strings, quotes and scenes all occur."""
    withheld = visible = closed = bare = quote = scene = 0
    for seed in range(CASES):
        rng = random.Random(seed)
        cp = _checkpoint(rng)
        snap = _snapshot(rng, cp)
        opened = view._opened(cp, snap, False)
        withheld += bool(opened.withheld)
        visible += bool(list(schema.iter_items(opened.checkpoint,
                                               dicts_only=False)))
        closed += snap.closed
        for _f, item in schema.iter_items(cp, dicts_only=False):
            bare += isinstance(item, str)
            quote += isinstance(item, dict) and "quote" in item
            scene += isinstance(item, dict) and "scene" in item
    assert min(withheld, visible, closed, bare, quote, scene) > 5
