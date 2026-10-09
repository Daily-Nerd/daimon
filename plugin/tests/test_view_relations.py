"""`view.relations`: the one place relation edges meet item text (#1132 PR 11b,
D11.7). The ledger holds no text; the endpoint join is a bounded `lookup_many`
and the erased-edge rule lives here, once, for the CLI and the viewer."""

import pytest

from daimon_briefing import normalize, relations, schema, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/view-relations"
A = "SENTINEL-a keep the fold deterministic"
B = "SENTINEL-b rebuild the index nightly"
C = "SENTINEL-c retire the old matcher"


def _checkpoint(sid, created, *texts):
    return {"session_id": sid, "created": created,
            "working_context": {"recent_decisions": [
                {"text": t, "trust": "inferred"} for t in texts]}}


def _write(sid="S-1", created="2026-08-01T00:00:00Z", texts=(A, B, C)):
    store.write_checkpoint(sid, _checkpoint(sid, created, *texts),
                           project_dir=PROJECT, writer=Writer.HUMAN)


def _id(text):
    for _fld, item in schema.iter_items(store.read_latest_body(
            project_dir=PROJECT, route=store.Route.OWN, admit=store.Admit.ANY)):
        if item.get("text") == text:
            return item["id"]
    for path in store.project_surfaces(PROJECT):
        import json
        body = json.loads(path.read_text())
        for _fld, item in schema.iter_items(body):
            if item.get("text") == text:
                return item["id"]
    raise AssertionError(text)


def _edge(frm, to, sid_from="S-1", sid_to="S-1"):
    return relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": sid_from, "field": "recent_decisions",
                       "item_id": frm},
        to_endpoint={"session_id": sid_to, "field": "recent_decisions",
                     "item_id": to},
        matched_by=["carry-absolute"], matcher_version="t-1",
        channel="lab-import", project_dir=PROJECT)


def _forget_id(item_id):
    store.append_event(item_id, "forgotten:" + normalize.content_key("x" + item_id),
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)


def test_the_join_returns_each_endpoint_text(tmp_checkpoint_dir):
    _write()
    a, b = _id(A), _id(B)
    rel = _edge(a, b)
    got = view.relations(PROJECT)
    assert [r["relation_id"] for r in got.rows] == [rel]
    assert got.texts == {a: A, b: B}
    assert got.withheld == 0


def test_a_quarantined_endpoint_reads_as_its_marker_and_the_edge_stays(
        tmp_checkpoint_dir):
    _write()
    a, b = _id(A), _id(B)
    _edge(a, b)
    qid = trust.propose(text=A, kind="decision", reason="fabricated",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=PROJECT)
    got = view.relations(PROJECT)
    assert len(got.rows) == 1
    assert got.texts[a] == f"[withheld: quarantine {qid}]"
    assert got.texts[b] == B
    assert A not in "".join(got.texts.values())


def test_a_closed_snapshot_marks_every_endpoint(tmp_checkpoint_dir):
    _write()
    a, b = _id(A), _id(B)
    _edge(a, b)
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    got = view.relations(PROJECT)
    assert len(got.rows) == 1
    assert set(got.texts.values()) == {"[withheld: trust ledger unreadable]"}


def test_an_edge_to_a_forgotten_endpoint_is_dropped_silently(tmp_checkpoint_dir):
    _write()
    a, b, c = _id(A), _id(B), _id(C)
    keep = _edge(a, b)
    _edge(b, c)
    _forget_id(c)
    got = view.relations(PROJECT)
    assert [r["relation_id"] for r in got.rows] == [keep]
    assert got.withheld == 0
    assert c not in got.texts and C not in "".join(got.texts.values())


def test_an_endpoint_that_only_survives_in_prev_n_still_resolves(
        tmp_checkpoint_dir):
    _write(texts=(A, B))
    a, b = _id(A), _id(B)
    _write(sid="S-2", created="2026-08-02T00:00:00Z", texts=(B,))
    _edge(a, b, sid_from="S-1", sid_to="S-2")
    got = view.relations(PROJECT)
    assert got.texts[a] == A


def test_an_endpoint_nothing_holds_stays_out_of_texts(tmp_checkpoint_dir):
    _write()
    a = _id(A)
    _edge(a, "r-feedfeed0001")
    got = view.relations(PROJECT)
    assert len(got.rows) == 1 and set(got.texts) == {a}


def test_the_join_is_one_lookup_for_the_union_of_endpoint_ids(
        tmp_checkpoint_dir, monkeypatch):
    _write()
    a, b, c = _id(A), _id(B), _id(C)
    _edge(a, b)
    _edge(b, c)
    real = view.lookup_many
    calls = []

    def spy(project, ids, **kw):
        calls.append(list(ids))
        return real(project, ids, **kw)

    monkeypatch.setattr(view, "lookup_many", spy)
    view.relations(PROJECT)
    assert len(calls) == 1 and sorted(calls[0]) == sorted([a, b, c])


def test_no_edge_means_no_lookup(tmp_checkpoint_dir, monkeypatch):
    _write()
    monkeypatch.setattr(view, "lookup_many",
                        lambda *a, **k: pytest.fail("no endpoint to join"))
    got = view.relations(PROJECT)
    assert got.rows == () and got.texts == {} and got.withheld == 0


def test_states_filter_and_order_keep_candidates_first(tmp_checkpoint_dir):
    _write()
    a, b, c = _id(A), _id(B), _id(C)
    confirmed = _edge(a, b)
    relations.confirm(confirmed, channel="cli-tty", project_dir=PROJECT)
    candidate = _edge(b, c)
    got = view.relations(PROJECT)
    assert [r["relation_id"] for r in got.rows] == [candidate, confirmed]
    only = view.relations(PROJECT, states={"confirmed"})
    assert [r["relation_id"] for r in only.rows] == [confirmed]
    with pytest.raises(relations.RelationError):
        view.relations(PROJECT, states={"vibes"})


def test_item_id_keeps_the_confirmed_edges_touching_it(tmp_checkpoint_dir):
    _write()
    a, b, c = _id(A), _id(B), _id(C)
    confirmed = _edge(a, b)
    relations.confirm(confirmed, channel="cli-tty", project_dir=PROJECT)
    _edge(a, c)                                   # stays a candidate
    got = view.relations(PROJECT, item_id=a)
    assert [r["relation_id"] for r in got.rows] == [confirmed]
    assert got.texts == {a: A, b: B}
    assert view.relations(PROJECT, item_id="").rows == ()
    assert view.relations(PROJECT, item_id="r-feedbeef1234").rows == ()


def test_a_forgotten_requested_item_answers_nothing(tmp_checkpoint_dir):
    _write()
    a, b = _id(A), _id(B)
    rel = _edge(a, b)
    relations.confirm(rel, channel="cli-tty", project_dir=PROJECT)
    _forget_id(a)
    got = view.relations(PROJECT, item_id=a)
    assert got.rows == () and got.texts == {} and got.withheld == 0


def test_a_chunk_of_many_ids_is_one_bounded_call(tmp_checkpoint_dir, monkeypatch):
    """The join is bounded by the edge count: 600 edges name 1200 ids and
    still make exactly one `lookup_many` call."""
    _write()
    a = _id(A)
    seen = []
    real = view.lookup_many
    monkeypatch.setattr(view, "lookup_many",
                        lambda p, ids, **k: (seen.append(len(list(ids))),
                                             real(p, ids, **k))[1])
    monkeypatch.setattr(
        relations, "listing",
        lambda **k: [{"relation_id": f"rel-{i:016x}", "state": "candidate",
                      "from": {"item_id": f"r-{i:012x}"},
                      "to": {"item_id": a}} for i in range(600)])
    got = view.relations(PROJECT)
    assert len(got.rows) == 600 and seen == [601]


def test_the_tombstone_id_rule_lives_in_the_store(tmp_checkpoint_dir):
    _forget_id("r-abc123456789")
    assert "r-abc123456789" in store.tombstoned_item_ids(project_dir=PROJECT)
    assert "r-def123456789" not in store.tombstoned_item_ids(project_dir=PROJECT)
    assert not hasattr(relations, "tombstoned_item_ids")
    assert not hasattr(relations, "endpoint_texts")
    assert not hasattr(relations, "_withhold_erased")


def test_one_relation_by_id_joins_its_endpoints_or_vanishes(tmp_checkpoint_dir):
    _write()
    a, b, c = _id(A), _id(B), _id(C)
    shown = _edge(a, b)
    gone = _edge(b, c)
    got = view.relations(PROJECT, relation_id=shown)
    assert [r["relation_id"] for r in got.rows] == [shown]
    assert got.texts == {a: A, b: B}
    _forget_id(c)
    assert view.relations(PROJECT, relation_id=gone).rows == ()
    assert view.relations(PROJECT, relation_id="rel-" + "0" * 16).rows == ()
