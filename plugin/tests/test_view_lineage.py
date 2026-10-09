"""`view.lineage` and `view.item_events` (#1132 PR 11a): the generation-keyed
appearances of one id, its judged events in the fold's order and the folded
lifecycle word, over ONE full snapshot. Stores come from the real writers; the
one hand-shaped file is an events ledger whose rows are out of order."""

import json

from daimon_briefing import carry, config, normalize, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/lineage"
SLUG = store.project_slug(PROJECT)
ITEM = "r-aaaaaaaaaaaa"
OLD = "the retry budget stays at six attempts per request"
NEW = "the retry budget is now four attempts per request"


def _cp(sid, created, decisions=()):
    return {"session_id": sid, "created": created, "author": "alice",
            "working_context": {
                "active_topic": {"text": "lineage", "trust": "inferred"},
                "recent_decisions": [dict(d, trust="inferred")
                                     for d in decisions]},
            "epistemic_snapshot": {}}


def _write(sid, created, decisions=()):
    store.write_checkpoint(sid, _cp(sid, created, decisions),
                           project_dir=PROJECT, writer=Writer.HUMAN)


def _events_path():
    return config.checkpoint_dir() / SLUG / "events.jsonl"


def _rows(*rows):
    _events_path().parent.mkdir(parents=True, exist_ok=True)
    with _events_path().open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _restated():
    _write("S-1", "2026-09-01T10:00:00Z", [{"text": OLD, "id": ITEM}])
    _write("S-2", "2026-09-02T10:00:00Z", [{"text": NEW, "id": ITEM}])


# ---- appearances ------------------------------------------------------------


def test_appearances_are_newest_first_and_keyed_by_pointer(tmp_checkpoint_dir):
    _restated()
    got = view.lineage(PROJECT, ITEM)
    assert [(a.index, a.ref, a.pointer_file) for a in got.appearances] == [
        (0, "latest", "latest.json"), (1, "prev-1", "prev-1.json")]
    assert [a.session_id for a in got.appearances] == ["S-2", "S-1"]
    assert [a.created for a in got.appearances] == [
        "2026-09-02T10:00:00Z", "2026-09-01T10:00:00Z"]
    assert all(isinstance(a.verdict, view.Visible) for a in got.appearances)
    assert isinstance(got.verdict, view.Found)
    assert got.verdict.item["text"] == NEW


def test_trust_and_carried_from_come_from_the_visible_copy(tmp_checkpoint_dir):
    first = _cp("S-1", "2026-09-01T10:00:00Z",
                [{"text": OLD, "id": ITEM}])
    store.write_checkpoint("S-1", first, project_dir=PROJECT,
                           writer=Writer.HUMAN)
    second = carry.merge(_cp("S-2", "2026-09-02T10:00:00Z"), first,
                         now=store._created_epoch("2026-09-02T10:00:00Z"))
    store.write_checkpoint("S-2", second, project_dir=PROJECT,
                           writer=Writer.HUMAN)
    got = view.lineage(PROJECT, ITEM)
    newest, oldest = got.appearances
    assert (oldest.trust, oldest.carried_from) == ("inferred", None)
    assert (newest.trust, newest.carried_from) == ("inferred", "S-1")


def test_a_withheld_appearance_names_no_trust_and_no_carry(tmp_checkpoint_dir):
    _restated()
    trust.propose(text=OLD, kind="decision", reason="fabricated finding",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    newest, oldest = view.lineage(PROJECT, ITEM).appearances
    assert isinstance(newest.verdict, view.Visible)
    assert isinstance(oldest.verdict, view.Withheld)
    assert oldest.verdict.reason == "quarantine"
    assert (oldest.trust, oldest.carried_from) == (None, None)
    assert oldest.session_id == "S-1"       # identity is not the value


def test_the_window_facts_are_listed_with_the_torn_pointers(tmp_checkpoint_dir):
    _restated()
    (config.checkpoint_dir() / SLUG / "prev-2.json").write_text("{torn")
    got = view.lineage(PROJECT, ITEM)
    assert got.refs == ("latest", "prev-1", "prev-2")
    assert got.unreadable == ("prev-2",)
    assert got.sessions == frozenset({"S-1", "S-2"})


def test_an_absent_id_has_an_empty_lineage(tmp_checkpoint_dir):
    _restated()
    got = view.lineage(PROJECT, "r-ffffffffffff")
    assert got.appearances == () and got.events == ()
    assert got.lifecycle == "active" and isinstance(got.verdict, view.Absent)


def test_a_project_with_no_bucket_has_no_lineage(tmp_checkpoint_dir):
    got = view.lineage("/p/no-bucket", ITEM)
    assert got.appearances == () and got.refs == ()


# ---- events -----------------------------------------------------------------


def test_events_follow_the_fold_order_not_the_file_order(tmp_checkpoint_dir):
    _restated()
    _rows(
        {"ts": "2026-09-05T10:00:00Z", "kind": "resolution", "item_ref": ITEM,
         "status": "resolved", "source": "cli"},
        {"item_ref": ITEM, "status": "unstamped first", "source": "cli"},
        {"ts": "2026-09-03T10:00:00Z", "kind": "resolution", "item_ref": ITEM,
         "status": "reopened", "source": "cli"},
        {"ts": "2026-09-03T10:00:00Z", "kind": "resolution", "item_ref": ITEM,
         "status": "same-second, later in the file", "source": "cli"},
        {"ts": "2026-09-04T10:00:00Z", "kind": "resolution",
         "item_ref": "r-bbbbbbbbbbbb", "status": "another item"})
    got = view.item_events(PROJECT, ITEM)
    assert [e.status for e in got] == [
        "unstamped first", "reopened", "same-second, later in the file",
        "resolved"]
    assert view.lineage(PROJECT, ITEM).events == got


def test_events_are_judged_rows_never_raw_ones(tmp_checkpoint_dir):
    _restated()
    key = normalize.content_key("a value that was forgotten earlier on")
    _rows({"ts": "2026-09-05T10:00:00Z", "kind": "tombstone",
           "item_ref": ITEM, "status": f"forgotten:{key}", "source": "cli"},
          {"ts": "2026-09-04T10:00:00Z", "kind": "resolution",
           "item_ref": ITEM, "status": "resolved", "source": "cli",
           "note": f"[forgotten:{key}]", "item_text": f"[forgotten:{key}]"})
    got = view.item_events(PROJECT, ITEM)
    assert [(e.status, e.tombstone) for e in got] == [
        ("resolved", False), ("forgotten", True)]
    assert got[0].note is None and got[0].item_text is None
    assert key not in repr(got)


def test_a_quarantined_note_is_the_marker(tmp_checkpoint_dir):
    _restated()
    qid = trust.propose(text=OLD, kind="decision", reason="fabricated",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=PROJECT)
    _rows({"ts": "2026-09-04T10:00:00Z", "kind": "resolution",
           "item_ref": ITEM, "status": "resolved", "source": "cli",
           "note": OLD})
    (event,) = view.item_events(PROJECT, ITEM)
    assert OLD not in repr(event) and qid in (event.note or "")


def test_an_event_ref_is_exact_so_a_corroboration_row_never_answers(
        tmp_checkpoint_dir):
    _restated()
    store.append_event(store.corroboration_ref(ITEM), "corroborated-by:S-9",
                       source="serialize", project_dir=PROJECT,
                       writer=Writer.HUMAN)
    assert view.item_events(PROJECT, ITEM) == ()


def test_events_without_a_bucket_or_a_ref_are_empty(tmp_checkpoint_dir):
    assert view.item_events("/p/no-bucket", ITEM) == ()
    _restated()
    assert view.item_events(PROJECT, "") == ()


def test_a_torn_ledger_line_costs_the_reader_nothing(tmp_checkpoint_dir):
    _restated()
    _events_path().write_text(
        "{not json\n" + json.dumps({"item_ref": ITEM, "status": "resolved"})
        + "\n", encoding="utf-8")
    assert [e.status for e in view.item_events(PROJECT, ITEM)] == ["resolved"]


# ---- lifecycle --------------------------------------------------------------


def _life(*rows):
    _rows(*rows)
    return view.lineage(PROJECT, ITEM).lifecycle


def test_the_lifecycle_word_is_the_latest_events_class(tmp_checkpoint_dir):
    _restated()
    assert view.lineage(PROJECT, ITEM).lifecycle == "active"
    assert _life({"ts": "2026-09-03T10:00:00Z", "item_ref": ITEM,
                  "status": "resolved"}) == "resolved"
    assert _life({"ts": "2026-09-04T10:00:00Z", "item_ref": ITEM,
                  "status": "superseded-by:r-bbbbbbbbbbbb"}) == "superseded"
    key = normalize.content_key("anything at all that was forgotten")
    assert _life({"ts": "2026-09-05T10:00:00Z", "kind": "tombstone",
                  "item_ref": ITEM, "status": f"forgotten:{key}"}
                 ) == "forgotten"
    assert _life({"ts": "2026-09-06T10:00:00Z", "item_ref": ITEM,
                  "status": "reopened"}) == "active"


def test_a_free_form_status_that_starts_like_a_tombstone_is_resolved(
        tmp_checkpoint_dir):
    _restated()
    assert _life({"ts": "2026-09-03T10:00:00Z", "item_ref": ITEM,
                  "status": "forgotten about it"}) == "resolved"


def test_events_with_no_surviving_copy_still_give_a_lifecycle(
        tmp_checkpoint_dir):
    _write("S-1", "2026-09-01T10:00:00Z")
    _rows({"ts": "2026-09-03T10:00:00Z", "item_ref": ITEM,
           "status": "resolved"})
    got = view.lineage(PROJECT, ITEM)
    assert isinstance(got.verdict, view.Absent)
    assert got.lifecycle == "resolved" and len(got.events) == 1


# ---- cost -------------------------------------------------------------------


def test_lineage_pays_one_full_snapshot_and_one_window(tmp_checkpoint_dir,
                                                       monkeypatch):
    _restated()
    calls = {"snapshot": 0, "pointers": 0, "judge": 0}
    for name in calls:
        real = getattr(view, name)

        def spy(*a, _real=real, _name=name, **k):
            calls[_name] += 1
            return _real(*a, **k)

        monkeypatch.setattr(view, name, spy)
    view.lineage(PROJECT, ITEM)
    assert calls == {"snapshot": 1, "pointers": 1, "judge": 0}
