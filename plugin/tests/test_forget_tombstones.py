"""PR 12: a forget writes the id row, the key row and every sibling row in one
append, and a reopen on the id can no longer lift the value.

The key row lives on a `forgotten-key:` ref of its own, so the fold that a
reopen acts on never sees it. Hostile shapes (a torn key row) are planted with
a plain byte writer; the real forget verb makes everything else.
"""
import json

from daimon_briefing import (cli, config, forget, jsonl, normalize, policy,
                             relations, store, view)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-tombstones"
VALUE = "zqxtombstone8821 the signing key rotates on the first of the month"
KEEPER = "an unrelated decision that must survive"
KEY = normalize.content_key(VALUE)


def _item(text):
    return {"text": text, "trust": "inferred"}


def _seed():
    """The value as a decision and as an open question: two ids, one value."""
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [_item(VALUE), _item(KEEPER)],
                            "open_questions": [_item(VALUE)]},
    }, project_dir=PROJECT, writer=Writer.HUMAN)
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    decision = next(i["id"] for i in stored["working_context"]
                    ["recent_decisions"] if i["text"] == VALUE)
    question = next(i["id"] for i in stored["working_context"]
                    ["open_questions"] if i["text"] == VALUE)
    assert decision != question
    return decision, question


def _events():
    path = store._events_path(store._resolved(PROJECT))
    return path, jsonl.read(path).rows


def _tombstone_rows():
    return [r for _p, rows in [_events()] for r in rows
            if r.get("kind") == "tombstone"]


# ---- the three kinds of row, in one append ----------------------------------

def test_a_forget_writes_the_id_row_then_the_key_row_then_the_siblings(
        tmp_checkpoint_dir, monkeypatch):
    decision, question = _seed()
    appends = []
    real = jsonl.append_lines
    monkeypatch.setattr(jsonl, "append_lines", lambda path, lines, **kw: (
        appends.append((path.name, list(lines))), real(path, lines, **kw))[1])
    assert cli.main(["forget", decision, "--reason", "stale",
                     "--project", PROJECT]) == 0
    events_appends = [lines for name, lines in appends
                      if name == "events.jsonl"]
    tomb = [json.loads(line) for lines in events_appends for line in lines
            if '"tombstone"' in line]
    assert len(events_appends) == 1 and len(events_appends[0]) == 3, \
        "the three rows land in ONE append"
    id_row, key_row, sibling_row = tomb
    assert (id_row["item_ref"], id_row["note"]) == (decision, "stale")
    assert key_row["item_ref"].startswith("forgotten-key:")
    assert sibling_row["item_ref"] == question
    for row in tomb:
        assert row["status"] == f"forgotten:{KEY}"
        assert row["kind"] == "tombstone"
    assert "note" not in key_row and "note" not in sibling_row


def test_the_key_ref_is_random_and_carries_nothing_of_the_key(
        tmp_checkpoint_dir):
    decision, _ = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    [key_row] = [r for r in _tombstone_rows()
                 if store.is_key_ref(r["item_ref"])]
    token = key_row["item_ref"].removeprefix("forgotten-key:")
    assert len(token) == 16 and int(token, 16) >= 0
    assert KEY not in key_row["item_ref"] and token not in KEY


def test_every_row_goes_through_the_admission_seam(tmp_checkpoint_dir,
                                                   monkeypatch):
    decision, _ = _seed()
    admitted = []
    real = policy.admit_row
    monkeypatch.setattr(policy, "admit_row", lambda row, **kw: (
        admitted.append(row.get("item_ref")), real(row, **kw))[1])
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    assert len(admitted) == 3 and admitted[0] == decision
    assert store.is_key_ref(admitted[1])


def test_a_second_write_for_the_same_value_adds_no_second_key_row(
        tmp_checkpoint_dir):
    decision, question = _seed()
    target = forget.Target(KEY, decision, (decision, question), PROJECT,
                           reason="again")
    assert forget.write_tombstones(target).reached
    before = len(_tombstone_rows())
    again = forget.write_tombstones(target)
    assert again.reached and len(_tombstone_rows()) == before
    standing = store.project_standing(PROJECT)[KEY]
    assert len(standing.key_refs) == 1
    assert standing.ids == tuple(sorted((decision, question)))


def test_the_rows_land_while_the_kill_switch_is_on(tmp_checkpoint_dir,
                                                   monkeypatch):
    decision, question = _seed()
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    target = forget.Target(KEY, decision, (decision, question), PROJECT)
    assert forget.write_tombstones(target).reached
    assert len(_tombstone_rows()) == 3


def test_an_unknown_project_writes_nothing_and_is_not_reached(
        tmp_checkpoint_dir):
    got = forget.write_tombstones(forget.Target(KEY, "i-1", ("i-1",), ""))
    assert got.reached is False


def test_the_append_gate_still_refuses_a_forgotten_status_from_other_writers(
        tmp_checkpoint_dir):
    _seed()
    assert store.append_events(
        [{"item_ref": "forgotten-key:00aa11bb22cc33dd",
          "status": f"forgotten:{KEY}", "kind": "tombstone"}],
        project_dir=PROJECT, writer=Writer.HUMAN) is False
    assert store.append_event(
        "i-1", f"forgotten:{KEY}", project_dir=PROJECT,
        writer=Writer.HUMAN) is False


# ---- a reopen on the id no longer lifts the value ----------------------------

def test_a_reopen_on_the_id_leaves_the_value_withheld(tmp_checkpoint_dir):
    decision, question = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    assert KEY in store.forgotten_content_keys(PROJECT)
    for ref in (decision, question):
        store.append_event(ref, "reopen", project_dir=PROJECT,
                           writer=Writer.HUMAN)
    assert KEY in store.forgotten_content_keys(PROJECT)
    assert KEY in store.all_forgotten_content_keys()
    standing = store.project_standing(PROJECT)[KEY]
    assert standing.ids == () and len(standing.key_refs) == 1
    assert view.forgotten_ids(store.resolutions(project_dir=PROJECT)) \
        == frozenset()


def test_a_legacy_one_row_tombstone_is_still_lifted_by_a_reopen(
        tmp_checkpoint_dir):
    decision, _ = _seed()
    assert store.append_event(
        decision, f"forgotten:{KEY}", kind="tombstone", project_dir=PROJECT,
        allow_disabled=True, tombstone=True, writer=Writer.HUMAN)
    assert KEY in store.forgotten_content_keys(PROJECT)
    store.append_event(decision, "reopen", project_dir=PROJECT,
                       writer=Writer.HUMAN)
    assert KEY not in store.forgotten_content_keys(PROJECT)


# ---- hostile rows, planted with the byte writer ------------------------------

def test_a_torn_key_row_degrades_the_ledger_and_the_id_row_still_withholds(
        tmp_checkpoint_dir):
    decision, _ = _seed()
    store.append_event(decision, "resolved", project_dir=PROJECT,
                       writer=Writer.HUMAN)         # creates the ledger
    path = store._events_path(store._resolved(PROJECT))
    good = {"ts": "2099-01-01T00:00:00Z", "kind": "tombstone",
            "item_ref": decision, "status": f"forgotten:{KEY}",
            "source": "cli"}
    with open(path, "ab") as handle:               # independent byte writer
        handle.write((json.dumps(good) + "\n").encode())
        handle.write(b'{"ts": "2099-01-01T00:00:00Z", "kind": "tombstone", '
                     b'"item_ref": "forgotten-key:00aa')
    read = jsonl.read(path)
    assert read.health is jsonl.Health.DEGRADED and read.torn == 1
    assert KEY in store.forgotten_content_keys(PROJECT)
    standing = store.project_standing(PROJECT)[KEY]
    assert standing.ids == (decision,) and standing.key_refs == ()


# ---- the siblings are reached by id ------------------------------------------

def _edge(a, b):
    return relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S2", "field": "recent_decisions",
                       "item_id": a},
        to_endpoint={"session_id": "S1", "field": "open_questions",
                     "item_id": b},
        matched_by=["carry-absolute"], matcher_version="lineage-v1",
        channel="lab-import", project_dir=PROJECT)


def test_a_relation_that_touches_only_a_sibling_is_removed(tmp_checkpoint_dir):
    decision, question = _seed()
    keep = _edge("r-aaaaaaaaaaaa", "r-bbbbbbbbbbbb")
    gone = _edge("r-cccccccccccc", question)
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    records = relations.records(project_dir=PROJECT)
    assert keep in records and gone not in records


# ---- the activity feed never shows a key row ---------------------------------

def test_the_event_feed_carries_no_key_row(tmp_checkpoint_dir):
    decision, _ = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    assert any(store.is_key_ref(r["item_ref"]) for r in _tombstone_rows())
    slug = store.project_slug(config.resolve_project_dir(PROJECT))
    shown = view.events(slug)
    assert not [e for e in shown if store.is_key_ref(e.item_ref)]
    assert [e.item_ref for e in shown if e.tombstone] \
        and all(e.item_ref in (decision,) or not store.is_key_ref(e.item_ref)
                for e in shown)
    assert KEY not in json.dumps([vars(e) for e in shown], default=str)
