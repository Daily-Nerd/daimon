"""Every registry ledger x every state, read through the view
(#1132 PR 10a, D10.9, read half).

States: ABSENT, DEGRADED, TRANSIENT and three kinds of UNREADABLE (a garbage
line, an undecodable byte, an OS error). Bytes express ABSENT, DEGRADED,
garbage and undecodable, and an independent byte writer plants them (never a
package appender); TRANSIENT and OS error go through the `jsonl.read` seam.
Each cell is asserted through `view`: the health the snapshot reports, the
registry posture, the note code and its hint, `closed`, and (for the events
ledger) whether the bucket's recall index is closed and the forget set is
named incomplete.
"""

import pytest

from daimon_briefing import config, jsonl, store, surfaces, view
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import ReadPosture

PROJECT = "/p/matrix"
LEDGERS = surfaces.bucket_ledger_names()

# state -> (how it is made, expected Health, hint fragment)
STATES = {
    "absent": ("none", Health.ABSENT, None),
    "degraded": (b'{"torn": "cut off mid-r', Health.DEGRADED, "ledger repair"),
    "garbage": (b"<<<<<<< HEAD\n", Health.UNREADABLE, "ledger repair"),
    "undecodable": (b"\xff\xfe not utf-8\n", Health.UNREADABLE,
                    "ledger repair"),
    "transient": ("seam", Health.TRANSIENT, "retry"),
    "os-error": ("seam", Health.UNREADABLE, "check permissions (EIO)"),
}
SEAM = {
    "transient": jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"),
    "os-error": jsonl.Read(Health.UNREADABLE, [], detail="EIO"),
}


def _bucket():
    return config.checkpoint_dir() / store.project_slug(PROJECT)


def _make(monkeypatch, name, state):
    how = STATES[state][0]
    bucket = _bucket()
    bucket.mkdir(parents=True, exist_ok=True)   # the bucket exists in every cell
    if how == "none":
        return
    if how == "seam":
        real = jsonl.read
        target = bucket / name
        monkeypatch.setattr(
            jsonl, "read",
            lambda p, *a, **k: SEAM[state] if p == target else real(p, *a, **k))
        return
    with open(bucket / name, "ab") as handle:       # not a package appender
        handle.write(how)


CELLS = [(name, state) for name in LEDGERS for state in STATES]


@pytest.mark.parametrize("name,state", CELLS)
def test_the_snapshot_applies_the_registry_posture(
        tmp_checkpoint_dir, monkeypatch, name, state):
    _make(monkeypatch, name, state)
    expected = STATES[state][1]
    snap = view.snapshot(PROJECT)
    assert snap.health[name] is expected
    posture = surfaces.bucket_ledger(name).read[
        surfaces.READ_STATES.index(expected.value)] \
        if expected is not Health.OK else ReadPosture.OPEN
    assert view.posture(name, expected) is posture
    mine = [n for n in snap.notes() if n.startswith(f"⚠ {name} is ")]
    if posture is ReadPosture.OPEN:
        assert mine == []
    else:
        assert len(mine) == 1
        assert f"is {expected.value}" in mine[0]
        hint = STATES[state][2]
        if name == "trust.jsonl":
            hint = hint.replace("ledger repair", "trust repair")
        assert hint in mine[0]
    # `closed` is the registry's CLOSED, nothing else
    assert snap.closed is (name == "trust.jsonl"
                           and posture is ReadPosture.CLOSED)
    # a note never carries a path or a value
    assert str(config.checkpoint_dir()) not in "\n".join(snap.notes())


@pytest.mark.parametrize("name,state", [c for c in CELLS
                                        if c[0] in ("events.jsonl",
                                                    "trust.jsonl")])
def test_the_light_snapshot_agrees_with_the_full_one(
        tmp_checkpoint_dir, monkeypatch, name, state):
    _make(monkeypatch, name, state)
    full = view.snapshot(PROJECT)
    light = view.judge(store.project_slug(PROJECT)).snap
    assert light.health[name] is full.health[name]
    assert light.closed is full.closed
    assert light.index_closed is full.index_closed


@pytest.mark.parametrize("state", sorted(STATES))
def test_an_unproven_events_ledger_closes_the_index_and_names_the_bucket(
        tmp_checkpoint_dir, monkeypatch, state):
    _make(monkeypatch, "events.jsonl", state)
    unproven = STATES[state][1] in (Health.TRANSIENT, Health.UNREADABLE)
    slug = store.project_slug(PROJECT)
    assert view.judge(slug).index_closed is unproven
    assert (slug in store.forgotten_incomplete()) is unproven
    assert bool(view.forgotten_notes()) is unproven
    assert view.snapshot(PROJECT).index_closed is unproven
    # never CLOSED for the briefing: the bucket reads through its good lines
    assert view.judge(slug).closed is False


@pytest.mark.parametrize("state", sorted(STATES))
def test_a_requests_ledger_of_another_bucket_is_skipped_only_when_unproven(
        tmp_checkpoint_dir, monkeypatch, state):
    from daimon_briefing import requests
    sender = "/p/matrix-sender"
    requests.open_request(to=store.project_slug(PROJECT), ask="ping",
                          why="because", channel="cli-agent",
                          project_dir=sender)
    how = STATES[state][0]
    target = (config.checkpoint_dir() / store.project_slug(sender)
              / "requests.jsonl")
    if how == "seam":
        real = jsonl.read
        monkeypatch.setattr(
            jsonl, "read",
            lambda p, *a, **k: SEAM[state] if p == target else real(p, *a, **k))
    elif how == "none":
        target.unlink()
    else:
        with open(target, "ab") as handle:
            handle.write(how)
    got = requests.inbox(PROJECT)
    skipped = STATES[state][1] in (Health.TRANSIENT, Health.UNREADABLE)
    degraded = STATES[state][1] is Health.DEGRADED
    assert bool(got.notes) is (skipped or degraded)
    if skipped:
        assert got.rows == []
    elif state != "absent":
        assert len(got.rows) == 1       # read around, nothing skipped
