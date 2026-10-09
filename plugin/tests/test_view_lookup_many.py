"""`view.lookup_many` / `view.lookup` (#1132 PR 11a): an exact-id read in four
tiers (pointer window, index locator, hints, absent). Stores are built by the
real writers; the index by `recall.rebuild`. A planted index row appears only
where the real writer cannot make the shape (a teammate's mirrored row)."""

import json
import sqlite3

import pytest

from daimon_briefing import (config, index_locate, normalize, recall, store,
                             trust, view)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/lookup-many"
SLUG = store.project_slug(PROJECT)
QID = "o-aaaaaaaaaaaa"          # an open question, carried in S-1 only
DID = "r-bbbbbbbbbbbb"          # a recent decision, in every session
GONE = "o-cccccccccccc"         # an id no checkpoint holds
TEXT_Q = "who owns the rollout of the new ingest pipeline"
TEXT_D = "adopt the strangler pattern for the billing split"


def _cp(sid, created, *, questions=(), decisions=(), project=PROJECT):
    return {"session_id": sid, "created": created, "author": "ada",
            "working_context": {
                "active_topic": {"text": f"topic of {sid}", "trust": "inferred"},
                "open_questions": [dict(q, trust="inferred") for q in questions],
                "recent_decisions": [dict(d, trust="inferred")
                                     for d in decisions]},
            "epistemic_snapshot": {}}


def _write(sid, created, project=PROJECT, **kw):
    store.write_checkpoint(sid, _cp(sid, created, project=project, **kw),
                           project_dir=project, writer=Writer.HUMAN)


def _bucket():
    return config.checkpoint_dir() / SLUG


def _q(text=TEXT_Q, item_id=QID):
    return {"text": text, "id": item_id}


def _d(text=TEXT_D, item_id=DID):
    return {"text": text, "id": item_id}


def _forget_id(item_id, text):
    store.append_event(item_id, "forgotten:" + normalize.content_key(text),
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)


def _quarantine(text, kind, item_id=""):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         item_id=item_id, project_dir=PROJECT)


@pytest.fixture
def window(tmp_checkpoint_dir):
    """Three sessions, the question only in the oldest, decision in all."""
    _write("S-1", "2026-08-01T00:00:00Z", questions=[_q()], decisions=[_d()])
    _write("S-2", "2026-08-02T00:00:00Z", decisions=[_d()])
    _write("S-3", "2026-08-03T00:00:00Z", decisions=[_d()])


@pytest.fixture
def aged_out(tmp_checkpoint_dir):
    """The question left the pointer window (history 3) but its session file
    and the index row remain."""
    _write("S-1", "2026-08-01T00:00:00Z", questions=[_q()], decisions=[_d()])
    for n in (2, 3, 4):
        _write(f"S-{n}", f"2026-08-0{n}T00:00:00Z", decisions=[_d()])
    recall.rebuild()


class Spy:
    def __init__(self, monkeypatch):
        self.calls = {}
        for mod, name in ((view, "pointers"), (view, "open_sessions"),
                          (view, "snapshot"), (view, "judge"),
                          (index_locate, "locate"),
                          (store, "read_checkpoint")):
            self._wrap(monkeypatch, mod, name)

    def _wrap(self, monkeypatch, mod, name):
        real = getattr(mod, name)
        key = f"{mod.__name__.rsplit('.', 1)[-1]}.{name}"
        self.calls[key] = 0

        def spy(*a, **k):
            self.calls[key] += 1
            return real(*a, **k)

        monkeypatch.setattr(mod, name, spy)


# ---- tier 1: the pointer window -------------------------------------------


def test_a_window_hit_needs_no_surface_walk(window, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not be reached")

    monkeypatch.setattr(store, "project_surfaces", boom)
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert isinstance(got, view.Found)
    assert got.item["text"] == TEXT_D and got.field.kind == "decision"
    assert got.source == "pointer" and got.notes == ()
    assert got.meta.session_id == "S-3" and got.meta.author == "ada"


def test_occurrences_are_distinct_sessions_newest_first(window):
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert [sid for sid, _created in got.occurrences] == ["S-3", "S-2", "S-1"]


def test_one_session_in_two_pointers_counts_once(window):
    latest = json.loads((_bucket() / "latest.json").read_text())
    (_bucket() / "prev-1.json").write_text(json.dumps(latest))   # an attach rewrite
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert [sid for sid, _created in got.occurrences].count("S-3") == 1


def test_the_newest_created_epoch_wins_not_the_pointer_order(window):
    """prev-1 is a newer session than latest: the epoch decides."""
    old = json.loads((_bucket() / "latest.json").read_text())
    newer = json.loads((_bucket() / "prev-1.json").read_text())
    old["session_id"], old["created"] = "S-old", "2026-07-01T00:00:00Z"
    old["working_context"]["recent_decisions"][0]["text"] = "the old wording"
    newer["session_id"], newer["created"] = "S-new", "2026-09-01T00:00:00Z"
    newer["working_context"]["recent_decisions"][0]["text"] = "the new wording"
    (_bucket() / "latest.json").write_text(json.dumps(old))
    (_bucket() / "prev-1.json").write_text(json.dumps(newer))
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert got.item["text"] == "the new wording"
    assert got.meta.session_id == "S-new"


def test_a_copy_with_no_usable_created_stamp_is_oldest(window):
    old = json.loads((_bucket() / "latest.json").read_text())
    old["created"] = "not a stamp"
    old["working_context"]["recent_decisions"][0]["text"] = "the unstamped one"
    (_bucket() / "latest.json").write_text(json.dumps(old))
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert got.item["text"] == TEXT_D


def test_a_withheld_newest_copy_is_withheld_with_no_text(window):
    _quarantine(TEXT_D, "decision")
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert isinstance(got, view.Withheld)
    assert (got.item_id, got.kind, got.reason) == (DID, "decision", "quarantine")
    assert got.quarantine_id and TEXT_D not in repr(got)


def test_a_forgotten_copy_in_the_window_carries_no_key(window):
    _forget_id(DID, TEXT_D)
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert isinstance(got, view.Withheld) and got.reason == "forgotten"
    assert got.value_key == ""              # the key never travels (scar 0119)


def test_an_unreadable_pointer_is_noted_on_what_it_could_hide(window):
    (_bucket() / "prev-2.json").write_text("{torn")
    got = view.lookup_many(PROJECT, [GONE, DID])
    assert isinstance(got[GONE], view.Absent)
    assert "pointer_unreadable" in got[GONE].notes
    assert isinstance(got[DID], view.Found) and got[DID].notes == ()


# ---- cost: snapshots, parses, calls ---------------------------------------


def test_a_batch_takes_one_judge_one_window_and_no_full_snapshot(
        window, monkeypatch):
    spy = Spy(monkeypatch)
    got = view.lookup_many(PROJECT, [DID, QID, GONE])
    assert set(got) == {DID, QID, GONE}
    assert spy.calls["view.judge"] == 1
    assert spy.calls["view.pointers"] == 1
    assert spy.calls["view.snapshot"] == 0
    assert spy.calls["store.read_checkpoint"] == 0
    assert spy.calls["index_locate.locate"] == 1


def test_a_caller_snapshot_is_used_and_no_judge_is_taken(window, monkeypatch):
    snap = view.snapshot(PROJECT)
    spy = Spy(monkeypatch)
    view.lookup_many(PROJECT, [DID], snap=snap)
    assert spy.calls["view.judge"] == 0 and spy.calls["view.snapshot"] == 0


def test_an_id_nothing_holds_costs_no_session_parse_and_no_rebuild(
        window, monkeypatch):
    spy = Spy(monkeypatch)
    rebuilds = []
    monkeypatch.setattr(recall, "rebuild", lambda: rebuilds.append(1))
    monkeypatch.setattr(recall, "_rebuild_forced",
                        lambda *a, **k: rebuilds.append(1))
    got = view.lookup_many(PROJECT, [GONE])[GONE]
    assert isinstance(got, view.Absent)
    assert spy.calls["view.open_sessions"] == 0
    assert spy.calls["store.read_checkpoint"] == 0
    assert rebuilds == []


# ---- tier 2: the locator ---------------------------------------------------


def test_an_aged_out_id_is_found_through_the_index_and_its_session_file(
        aged_out, monkeypatch):
    assert QID not in (_bucket() / "latest.json").read_text()
    spy = Spy(monkeypatch)
    got = view.lookup_many(PROJECT, [QID])[QID]
    assert isinstance(got, view.Found)
    assert got.item["text"] == TEXT_Q and got.source == "session"
    assert got.meta.session_id == "S-1"
    assert [sid for sid, _c in got.occurrences] == ["S-1"]
    assert spy.calls["index_locate.locate"] == 1


def test_the_index_adds_the_sessions_the_window_no_longer_holds(aged_out):
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert got.source == "pointer"
    assert [sid for sid, _c in got.occurrences] == ["S-4", "S-3", "S-2", "S-1"]


def test_several_located_ids_open_their_sessions_in_one_call(
        aged_out, monkeypatch):
    spy = Spy(monkeypatch)
    view.lookup_many(PROJECT, [QID, GONE])
    assert spy.calls["view.open_sessions"] == 1
    assert spy.calls["view.pointers"] == 1


def test_a_stale_index_row_is_skipped(aged_out):
    """The index names S-1, whose file no longer holds the id."""
    path = config.checkpoint_dir() / "S-1.json"
    body = json.loads(path.read_text())
    body["working_context"]["open_questions"] = []
    path.write_text(json.dumps(body))
    assert view.lookup_many(PROJECT, [QID])[QID] == view.Absent()


def test_a_located_item_is_judged_at_read_time(aged_out):
    """The index is behind the ledgers: the value is quarantined now."""
    _quarantine(TEXT_Q, "question")
    got = view.lookup_many(PROJECT, [QID])[QID]
    assert isinstance(got, view.Withheld) and got.reason == "quarantine"


def _plant_row(session_id, item_id, text, *, kind="question", slug=SLUG):
    conn = sqlite3.connect(str(config.recall_db()))
    conn.execute(
        "INSERT INTO items (text, quote, trust, kind, author, project_slug,"
        " session_id, created, item_id) VALUES (?, '', 'stated', ?, 'grace',"
        " ?, ?, 1785000000.0, ?)", (text, kind, slug, session_id, item_id))
    conn.commit()
    conn.close()


def test_a_team_mirror_row_is_answered_as_index_only(aged_out):
    _plant_row("T-1", "o-dddddddddddd", "a teammate's open question")
    got = view.lookup_many(PROJECT, ["o-dddddddddddd"])["o-dddddddddddd"]
    assert isinstance(got, view.Found) and got.source == "index"
    assert got.item["text"] == "a teammate's open question"
    assert got.item["id"] == "o-dddddddddddd"
    assert got.item["origin_session"] == "T-1"
    assert got.field.kind == "question"
    assert "index_only:team-mirror" in got.notes
    assert got.meta.author == "grace" and got.meta.session_id == "T-1"


def test_a_stampless_local_file_is_answered_as_index_only_legacy(aged_out):
    legacy = {"session_id": "L-1", "created": "2026-07-01T00:00:00Z",
              "working_context": {"open_questions": [
                  {"text": "a legacy open question", "id": "o-eeeeeeeeeeee"}]}}
    (config.checkpoint_dir() / "L-1.json").write_text(json.dumps(legacy))
    _plant_row("L-1", "o-eeeeeeeeeeee", "a legacy open question")
    got = view.lookup_many(PROJECT, ["o-eeeeeeeeeeee"])["o-eeeeeeeeeeee"]
    assert isinstance(got, view.Found) and got.source == "index"
    assert "index_only:pointer-attributed-legacy" in got.notes


def test_an_index_only_row_is_classified_at_read_time(aged_out):
    _plant_row("T-1", "o-dddddddddddd", TEXT_Q)
    _quarantine(TEXT_Q, "question")
    got = view.lookup_many(PROJECT, ["o-dddddddddddd"])["o-dddddddddddd"]
    assert isinstance(got, view.Withheld) and got.reason == "quarantine"


def test_an_unusable_index_is_a_note_not_a_failure(aged_out):
    config.recall_db().write_bytes(b"not a database" * 50)
    got = view.lookup_many(PROJECT, [QID])[QID]
    assert got == view.Absent(("index_unavailable",))


# ---- tier 3: hints ----------------------------------------------------------


def test_a_tombstone_only_id_is_forgotten_and_carries_no_key(window):
    _forget_id(GONE, "a value no checkpoint holds any more")
    got = view.lookup_many(PROJECT, [GONE])[GONE]
    assert got == view.Withheld(GONE, "unknown", "forgotten", None, "",
                                got.notes)
    assert got.value_key == ""


def test_a_lifted_tombstone_is_not_announced(window):
    _forget_id(GONE, "a value no checkpoint holds any more")
    assert store.append_event(GONE, "reopen", project_dir=PROJECT,
                              writer=Writer.HUMAN)
    assert isinstance(view.lookup_many(PROJECT, [GONE])[GONE], view.Absent)


def test_a_quarantine_naming_an_id_is_the_hint_for_an_aged_out_copy(window):
    qid = _quarantine("an old claim whose copy has aged out", "decision",
                      item_id="r-ffffffffffff")
    got = view.lookup_many(PROJECT, ["r-ffffffffffff"])["r-ffffffffffff"]
    assert isinstance(got, view.Withheld)
    assert (got.reason, got.quarantine_id, got.kind) == (
        "quarantine", qid, "decision")


def test_forgotten_outranks_a_quarantine_hint(window):
    _quarantine("an old claim whose copy has aged out", "decision",
                item_id="r-ffffffffffff")
    _forget_id("r-ffffffffffff", "an old claim whose copy has aged out")
    got = view.lookup_many(PROJECT, ["r-ffffffffffff"])["r-ffffffffffff"]
    assert got.reason == "forgotten"


def test_a_closed_trust_ledger_skips_the_index_and_the_hints(
        aged_out, monkeypatch):
    _forget_id(GONE, "a value no checkpoint holds any more")
    (_bucket() / "trust.jsonl").write_bytes(b"this is not json\n")

    def boom(*_a, **_k):
        raise AssertionError("closed trust must not consult the index")

    monkeypatch.setattr(index_locate, "locate", boom)
    got = view.lookup_many(PROJECT, [GONE, QID])
    assert got[GONE] == view.Absent(("index_closed",))
    assert got[QID] == view.Absent(("index_closed",))


def test_a_closed_snapshot_still_withholds_a_window_copy(window):
    (_bucket() / "trust.jsonl").write_bytes(b"this is not json\n")
    got = view.lookup_many(PROJECT, [DID])[DID]
    assert isinstance(got, view.Withheld) and got.reason == "closed"


# ---- lookup, shape ----------------------------------------------------------


def test_lookup_is_lookup_many_of_one(window):
    assert view.lookup(PROJECT, DID) == view.lookup_many(PROJECT, [DID])[DID]
    assert isinstance(view.lookup(PROJECT, GONE), view.Absent)


def test_an_unknown_project_is_absent_for_every_id(tmp_checkpoint_dir):
    got = view.lookup_many("/p/no-such-project", [GONE, DID])
    assert list(got) == [GONE, DID]
    assert all(isinstance(v, view.Absent) for v in got.values())


def test_no_ids_is_no_answer_and_no_read(window, monkeypatch):
    spy = Spy(monkeypatch)
    assert view.lookup_many(PROJECT, []) == {}
    assert sum(spy.calls.values()) == 0


def test_every_asked_id_is_a_key_once(window):
    got = view.lookup_many(PROJECT, [DID, DID, GONE])
    assert list(got) == [DID, GONE]
