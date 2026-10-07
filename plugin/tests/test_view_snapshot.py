"""`view.snapshot`: every ledger read once, health as data (#1132 PR 6a).

Stores are written by the real writers. Damaged bytes are planted by an
independent byte writer, never by an appender: a snapshot tested against the
writers it judges could only ever agree with them.
"""

import pytest

from daimon_briefing import (amendments, config, normalize, refutations,
                             requests, schema, store, surfaces, trust, view)
from daimon_briefing.jsonl import Health

PROJECT = "/p/view-snapshot"
OTHER = "/p/view-other"
SECRET = "the vault root token rotates on friday"
ITEM = "o-1234567890ab"


def _bucket():
    return config.checkpoint_dir() / store.project_slug(PROJECT)


def _plant(name, data: bytes):
    path = _bucket() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:                 # not a package appender
        handle.write(data)
    return path


def _forget(text, project=PROJECT):
    key = normalize.content_key(text)
    assert store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                              tombstone=True, project_dir=project)
    return key


def _quarantine(text, kind="decision"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def test_every_health_key_is_a_registered_bucket_ledger():
    for name in view.LEDGERS:
        assert surfaces.bucket_ledger(name) is not None, name


def test_an_unknown_project_is_all_absent_and_open(tmp_checkpoint_dir):
    snap = view.snapshot(None)
    assert set(snap.health) == set(view.LEDGERS)
    assert set(snap.health.values()) == {Health.ABSENT}
    assert snap.closed is False
    assert snap.notes() == ()
    assert snap.forgotten == frozenset() and snap.quarantined == frozenset()


def test_a_project_with_no_ledgers_is_absent_not_unreadable(tmp_checkpoint_dir):
    snap = view.snapshot(PROJECT)
    assert set(snap.health.values()) == {Health.ABSENT}
    assert snap.notes() == ()


def test_each_ledger_folds_into_its_field(tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    key = _forget(SECRET)
    q_id = _quarantine("adopt the plan nobody reviewed")
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-tty", project_dir=PROJECT)
    r_id = requests.open_request(to=store.project_slug(OTHER), ask="ping",
                                 why="because", channel="cli-agent",
                                 project_dir=PROJECT)
    refutations.assert_ruling(
        subject="public posts", verdict="no internal numbers",
        scope="publishing", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)
    snap = view.snapshot(PROJECT)
    assert "o-aaaaaa" in snap.resolutions
    assert key in snap.forgotten
    value_key = trust.value_key("adopt the plan nobody reviewed")
    assert snap.quarantined == frozenset({("decision", value_key)})
    assert snap.quarantine_ids[("decision", value_key)] == q_id
    assert a_id in {r["amendment_id"] for r in snap.amendments[ITEM]["rows"]}
    assert r_id in snap.requests
    assert snap.rulings.state == "read" and len(snap.rulings.rows) == 1
    assert snap.corroborations == {}
    assert set(snap.health.values()) == {Health.OK}
    assert snap.notes() == ()


def test_forgotten_is_the_union_of_every_local_project(tmp_checkpoint_dir):
    key = _forget(SECRET, project=OTHER)
    assert key in view.snapshot(PROJECT).forgotten


def test_a_torn_tail_is_degraded_and_noted_without_content(tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    _plant("events.jsonl", b'{"kind": "resolution", "item_ref": "o-bb')
    snap = view.snapshot(PROJECT)
    assert snap.health["events.jsonl"] is Health.DEGRADED
    assert "o-aaaaaa" in snap.resolutions
    assert snap.closed is False
    assert snap.notes() == ("⚠ events.jsonl is degraded (torn)",)


def test_an_undecodable_line_is_unreadable_but_the_good_rows_stay(
        tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    _plant("events.jsonl", b"\xff\xfe not utf-8\n")
    snap = view.snapshot(PROJECT)
    assert snap.health["events.jsonl"] is Health.UNREADABLE
    assert "o-aaaaaa" in snap.resolutions
    assert snap.notes()[0].startswith("⚠ events.jsonl is unreadable")


def test_an_unreadable_trust_ledger_closes_the_snapshot(tmp_checkpoint_dir):
    _quarantine("adopt the plan nobody reviewed")
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")
    snap = view.snapshot(PROJECT)
    assert snap.health["trust.jsonl"] is Health.UNREADABLE
    assert snap.closed is True


def test_a_degraded_trust_ledger_does_not_close_it(tmp_checkpoint_dir):
    _quarantine("adopt the plan nobody reviewed")
    _plant("trust.jsonl", b'{"cut": "half a row')
    snap = view.snapshot(PROJECT)
    assert snap.health["trust.jsonl"] is Health.DEGRADED
    assert snap.closed is False
    assert len(snap.quarantined) == 1


def test_a_directory_in_a_ledgers_place_is_unreadable(tmp_checkpoint_dir):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    (_bucket() / "amendments.jsonl").mkdir()
    snap = view.snapshot(PROJECT)
    assert snap.health["amendments.jsonl"] is Health.UNREADABLE
    assert snap.amendments == {}


def _boom(*args, **kwargs):
    raise RuntimeError("hand-edited ledger")


@pytest.mark.parametrize("target,attr,ledger", [
    (store, "fold_resolutions", "events.jsonl"),
    (store, "fold_corroborations", "events.jsonl"),
    (trust, "records", "trust.jsonl"),
    (amendments, "records", "amendments.jsonl"),
    (requests, "records", "requests.jsonl"),
])
def test_a_fold_that_raises_is_unreadable_for_that_ledger_only(
        tmp_checkpoint_dir, monkeypatch, target, attr, ledger):
    store.append_event("o-aaaaaa", "resolved", project_dir=PROJECT)
    _quarantine("adopt the plan nobody reviewed")
    monkeypatch.setattr(target, attr, _boom)
    snap = view.snapshot(PROJECT)
    assert snap.health[ledger] is Health.UNREADABLE
    assert snap.details[ledger] == "fold raised RuntimeError"
    unreadable = {n for n, h in snap.health.items() if h is Health.UNREADABLE}
    assert unreadable == {ledger}
    assert snap.closed is (ledger == "trust.jsonl")


def test_rulings_that_raise_are_unreadable_for_refutations_only(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import briefing
    monkeypatch.setattr(briefing, "rulings_read", _boom)
    snap = view.snapshot(PROJECT)
    assert snap.health["refutations.jsonl"] is Health.UNREADABLE
    assert snap.rulings is None and snap.closed is False


def test_the_snapshot_never_creates_a_file_or_a_directory(tmp_checkpoint_dir):
    before = sorted(p for p in tmp_checkpoint_dir.parent.rglob("*"))
    view.snapshot(PROJECT)
    assert sorted(p for p in tmp_checkpoint_dir.parent.rglob("*")) == before


def test_notes_are_empty_for_ok_and_absent_only():
    snap = view.Snapshot.empty()
    assert snap.notes() == ()
    snap = view.Snapshot.empty()
    import dataclasses
    from types import MappingProxyType
    health = dict(snap.health)
    health["trust.jsonl"] = Health.TRANSIENT
    snap = dataclasses.replace(snap, health=MappingProxyType(health),
                               details=MappingProxyType({"trust.jsonl": "EAGAIN"}))
    assert snap.notes() == ("⚠ trust.jsonl is transient (EAGAIN)",)


def test_the_snapshot_is_frozen():
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.Snapshot.empty().closed = True  # type: ignore[misc]
    assert schema.VALUE_FIELDS  # the snapshot keys on these via classify
