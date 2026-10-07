"""`view.suppressed` (#1132 PR 7b): what `status --suppressed` lists, typed.

Resolved loops (item, field, closing event), quarantined values (a
`Withheld`, never text), a count when the trust ledger cannot be read, the
#14 supersede candidates and the ledger-health notes. A forgotten value is
never listed and never counted. Stores are written by the real writers."""

import dataclasses

import pytest

from daimon_briefing import config, normalize, store, trust, view
from daimon_briefing.jsonl import Health

PROJECT = "/p/view-suppressed"
NOW = 1_800_000_000.0
CLOSED_Q = "should the exporter batch rows before writing the archive"
QUAR_Q = "who owns the quarantined migration"
FORGOTTEN_Q = "whether the forgotten cache survives restarts"
LIVE_Q = "a live question that stays open"


def _checkpoint():
    return {
        "session_id": "S-1", "created": "2026-09-09T00:00:00Z",
        "working_context": {
            "active_topic": {"text": "exporter", "trust": "inferred"},
            "open_questions": [
                {"text": CLOSED_Q, "trust": "inferred"},
                {"text": QUAR_Q, "trust": "inferred"},
                {"text": FORGOTTEN_Q, "trust": "inferred"},
                {"text": LIVE_Q, "trust": "inferred"}],
            "recent_decisions": [
                {"text": "adopt the strangler pattern for the exporter",
                 "trust": "inferred"}]},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def _seed():
    store.write_checkpoint("S-1", _checkpoint(), project_dir=PROJECT)
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    return {i["text"]: i["id"]
            for i in cp["working_context"]["open_questions"]}, cp


def _resolve(item_id, status="resolved", **kw):
    store.append_event(item_id, status, project_dir=PROJECT, **kw)


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="planted",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT)


def test_nothing_suppressed_is_empty(tmp_checkpoint_dir):
    _seed()
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == () and got.withheld == ()
    assert got.closed == 0 and got.candidates == () and got.notes == ()


def test_no_checkpoint_is_empty(tmp_checkpoint_dir):
    got = view.suppressed(PROJECT, NOW)
    assert got == view.Suppression((), (), 0, (), ())


def test_a_resolved_loop_is_listed_with_its_closing_event(tmp_checkpoint_dir):
    ids, _cp = _seed()
    _resolve(ids[CLOSED_Q], note="shipped in 0.9")
    got = view.suppressed(PROJECT, NOW)
    assert len(got.resolved) == 1
    row = got.resolved[0]
    assert row.item["id"] == ids[CLOSED_Q] and row.item["text"] == CLOSED_Q
    assert row.field.key == "open_questions"
    assert row.event["status"] == "resolved"
    assert row.event["note"] == "shipped in 0.9"
    assert got.withheld == ()


def test_a_quarantined_value_is_a_withheld_never_text(tmp_checkpoint_dir):
    ids, _cp = _seed()
    qid = _quarantine(QUAR_Q)
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == ()
    assert [(w.item_id, w.kind, w.reason, w.quarantine_id)
            for w in got.withheld] == [
        (ids[QUAR_Q], "question", "quarantine", qid)]
    assert QUAR_Q not in repr(got.withheld)


def test_a_forgotten_value_is_neither_listed_nor_counted(tmp_checkpoint_dir):
    ids, _cp = _seed()
    _forget(FORGOTTEN_Q)
    _resolve(ids[FORGOTTEN_Q])          # forgotten AND resolved: forgotten wins
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == () and got.withheld == () and got.closed == 0
    blob = repr(got)
    assert FORGOTTEN_Q not in blob and ids[FORGOTTEN_Q] not in blob


def test_a_quarantined_and_resolved_item_is_listed_once_as_withheld(
        tmp_checkpoint_dir):
    ids, _cp = _seed()
    _quarantine(QUAR_Q)
    _resolve(ids[QUAR_Q])
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == () and len(got.withheld) == 1


def test_an_unreadable_trust_ledger_counts_and_lists_nothing(
        tmp_checkpoint_dir):
    ids, _cp = _seed()
    _resolve(ids[CLOSED_Q])
    bucket = config.checkpoint_dir() / store.project_slug(PROJECT)
    with open(bucket / "trust.jsonl", "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == () and got.withheld == ()
    assert got.closed >= 5               # every item and the topic
    assert got.notes and got.notes[0].startswith("⚠ trust.jsonl is unreadable")
    assert CLOSED_Q not in repr(got) and ids[CLOSED_Q] not in repr(got)


def test_a_supersede_candidate_is_listed(tmp_checkpoint_dir):
    _ids, cp = _seed()
    decision = cp["working_context"]["recent_decisions"][0]
    _resolve(decision["id"], "supersede-candidate:r-9f3a2b")
    got = view.suppressed(PROJECT, NOW)
    assert got.resolved == ()
    [(key, item, evt)] = got.candidates
    assert key == "recent_decisions" and item["id"] == decision["id"]
    assert item["_supersede_candidate"] == "r-9f3a2b"
    assert evt["status"].startswith("supersede-candidate")


def test_a_quarantined_candidate_is_not_listed_as_one(tmp_checkpoint_dir):
    _ids, cp = _seed()
    decision = cp["working_context"]["recent_decisions"][0]
    _resolve(decision["id"], "supersede-candidate:r-9f3a2b")
    _quarantine(decision["text"], kind="decision")
    got = view.suppressed(PROJECT, NOW)
    assert got.candidates == ()
    assert len(got.withheld) == 1


def test_a_degraded_ledger_adds_a_note(tmp_checkpoint_dir):
    _seed()
    bucket = config.checkpoint_dir() / store.project_slug(PROJECT)
    with open(bucket / "events.jsonl", "ab") as fh:
        fh.write(b'{"kind": "resolution", "item_ref": "o-bb')
    got = view.suppressed(PROJECT, NOW)
    snap = view.snapshot(PROJECT)
    assert snap.health["events.jsonl"] is Health.DEGRADED
    assert got.notes == snap.notes() == ("⚠ events.jsonl is degraded (torn)",)


def test_suppressed_reads_the_own_bucket_only(tmp_checkpoint_dir):
    ids, _cp = _seed()
    _resolve(ids[CLOSED_Q])
    other = view.suppressed("/p/somebody-else", NOW)
    assert other == view.Suppression((), (), 0, (), ())


def test_the_suppression_is_frozen(tmp_checkpoint_dir):
    got = view.suppressed(PROJECT, NOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.closed = 1  # type: ignore[misc]
