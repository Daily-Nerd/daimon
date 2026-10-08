"""`view.events` and `view.verifications` (#1132 PR 8b-2).

The events ledger and the verification ledger as typed, frozen rows. The
declared prose columns of an event (`note`, `item_text`, `status`) are judged
over one snapshot: a quarantined or closed value is the withheld marker, a
forgotten one reads as absent, and a forget tombstone's status is the bare
word `forgotten`. Stores are built by the real writers."""

import dataclasses
import json

import pytest

from daimon_briefing import display, normalize, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/events"
SLUG = store.project_slug(PROJECT)
HIDE = "the plan that was never reviewed by anybody"
KEEP = "a note that stays visible to everyone"


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT, writer=Writer.HUMAN)


def _event(ref="o-aaaaaaaaaaaa", status="resolved", **kw):
    assert store.append_event(ref, status, project_dir=PROJECT, **kw, writer=Writer.HUMAN)


def _ledger(name):
    from daimon_briefing import config
    return config.checkpoint_dir() / SLUG / name


def _break_trust():
    with open(_ledger("trust.jsonl"), "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")


def _append_raw(name, *rows):
    path = _ledger(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write((row if isinstance(row, str) else json.dumps(row)) + "\n")


# ---- events ----------------------------------------------------------------


def test_events_are_typed_frozen_rows_in_file_order(tmp_checkpoint_dir):
    _event("o-aaaaaaaaaaaa", "resolved", note=KEEP, item_text="the closed goal",
           source="cli")
    _event("o-bbbbbbbbbbbb", "reopened")
    first, second = view.events(PROJECT)
    assert isinstance(first, view.Event)
    assert (first.item_ref, first.kind, first.status, first.source) == (
        "o-aaaaaaaaaaaa", "resolution", "resolved", "cli")
    assert (first.note, first.item_text) == (KEEP, "the closed goal")
    assert first.ts and first.tombstone is False
    assert (second.item_ref, second.note, second.item_text) == (
        "o-bbbbbbbbbbbb", None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.note = "x"


def test_no_events_ledger_is_no_events(tmp_checkpoint_dir):
    assert view.events(PROJECT) == ()
    assert view.verifications(PROJECT) == ()


@pytest.mark.parametrize("project", ["", None])
def test_no_project_has_no_rows(tmp_checkpoint_dir, project):
    assert view.events(project) == ()
    assert view.verifications(project) == ()


def test_torn_lines_and_non_object_rows_are_skipped(tmp_checkpoint_dir):
    _event("o-aaaaaaaaaaaa")
    _append_raw("events.jsonl", "{broken", "[1, 2]", "")
    assert [e.item_ref for e in view.events(PROJECT)] == ["o-aaaaaaaaaaaa"]


def test_a_column_that_is_not_text_reads_as_absent(tmp_checkpoint_dir):
    _append_raw("events.jsonl", {"ts": 5, "kind": ["x"], "item_ref": "o-1",
                                 "status": "", "source": None, "note": 3,
                                 "item_text": {"a": 1}})
    [e] = view.events(PROJECT)
    assert (e.ts, e.kind, e.status, e.source, e.note, e.item_text) == (
        None,) * 6
    assert e.item_ref == "o-1"


def test_a_quarantined_note_and_item_text_read_as_the_marker(tmp_checkpoint_dir):
    _event(note=HIDE, item_text=HIDE)
    rec = _quarantine(HIDE)
    [e] = view.events(PROJECT)
    marker = f"[withheld: quarantine {rec}]"
    assert (e.note, e.item_text) == (marker, marker)
    assert HIDE not in repr(e)


def test_a_forgotten_note_and_item_text_read_as_absent(tmp_checkpoint_dir):
    _event(note=HIDE, item_text=HIDE)
    _forget(HIDE)
    [first, _tombstone] = view.events(PROJECT)
    assert (first.note, first.item_text) == (None, None)


def test_a_quarantined_status_reads_as_the_marker(tmp_checkpoint_dir):
    _event(status=HIDE)
    rec = _quarantine(HIDE)
    [e] = view.events(PROJECT)
    assert e.status == f"[withheld: quarantine {rec}]"


def test_an_unreadable_trust_ledger_masks_item_text_and_keeps_the_prose(
        tmp_checkpoint_dir):
    """Notes are human prose and stay readable (as in the rulings lane); an
    item value is masked because nothing can be proven not quarantined."""
    _event(note=KEEP, item_text="the closed goal")
    _break_trust()
    [e] = view.events(PROJECT)
    assert e.note == KEEP and e.status == "resolved"
    assert e.item_text == "[withheld: trust ledger unreadable]"


def test_a_tombstone_is_the_bare_word_forgotten_with_no_key_or_text(
        tmp_checkpoint_dir):
    _forget(HIDE)
    key = normalize.content_key(HIDE)
    _append_raw("events.jsonl", {
        "ts": "2026-08-01T00:00:00Z", "kind": "tombstone",
        "item_ref": "i-2", "status": f"Forgotten:{key}", "source": "cli",
        "item_text": "plain text a tombstone should not carry"})
    rows = view.events(PROJECT)
    assert [e.status for e in rows] == ["forgotten", "forgotten"]
    assert [e.tombstone for e in rows] == [True, True]
    assert [e.item_text for e in rows] == [None, None]
    assert key not in repr(rows)


def test_a_scrubbed_field_never_shows_its_key(tmp_checkpoint_dir):
    key = normalize.content_key(HIDE)
    marker = store._FORGOTTEN_FIELD_MARKER.format(key)
    _append_raw("events.jsonl", {
        "ts": "2026-08-01T00:00:00Z", "kind": "resolution", "item_ref": "o-1",
        "status": f"reopen {marker}", "note": marker, "item_text": marker})
    [e] = view.events(PROJECT)
    assert (e.status, e.note, e.item_text) == ("reopen", None, None)
    assert key not in repr(e)


def test_a_status_that_is_only_a_scrubbed_value_reads_as_absent(
        tmp_checkpoint_dir):
    marker = store._FORGOTTEN_FIELD_MARKER.format("0123456789abcdef")
    _append_raw("events.jsonl", {"ts": "2026-08-01T00:00:00Z", "item_ref": "o-1",
                                 "status": marker})
    [e] = view.events(PROJECT)
    assert e.status is None


def test_events_judge_over_the_snapshot_they_are_given(tmp_checkpoint_dir):
    _event(note=HIDE)
    snap = view.snapshot(PROJECT)
    rec = _quarantine(HIDE)
    [stale] = view.events(PROJECT, snap=snap)
    assert stale.note == HIDE
    [fresh] = view.events(PROJECT)
    assert fresh.note == f"[withheld: quarantine {rec}]"
    assert display.withheld_marker(view.Withheld(
        None, "prose", "quarantine", rec, "")) == fresh.note


# ---- verifications ---------------------------------------------------------


def test_verifications_are_typed_frozen_rows(tmp_checkpoint_dir):
    assert store.append_verification("o-aaaaaaaaaaaa", "quote",
                                     "not-in-transcript", project_dir=PROJECT)
    _append_raw("verification.jsonl", "{torn", {"item_ref": 4, "check": "",
                                                "reason": "x"})
    first, second = view.verifications(PROJECT)
    assert isinstance(first, view.Verification)
    assert (first.item_ref, first.check, first.reason) == (
        "o-aaaaaaaaaaaa", "quote", "not-in-transcript")
    assert first.ts
    assert (second.item_ref, second.check, second.reason) == (None, None, "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.reason = "y"
