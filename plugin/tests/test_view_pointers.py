"""`view.pointers`, `view.sessions` and `view.open_sessions` (#1132 PR 8b-1).

The pointer window and the session listing behind the viewer's checkpoint,
history and diff routes. Pointers are judged like `view.open` (a withheld item
is removed and recorded, a closed trust ledger removes them all); the session
listing is LIGHT: it classifies only each session's topic and never copies or
filters a body. Stores are built by the real writers."""

import json

import pytest

from daimon_briefing import config, normalize, store, trust, view

PROJECT = "/p/ptr"
SLUG = store.project_slug(PROJECT)
HIDE = "the plan that was never reviewed by anybody"
KEEP = "an open question that stays visible"


def _cp(sid, created, *, topic="a topic", questions=(), project=PROJECT):
    return {"session_id": sid, "created": created, "author": "ada",
            "working_context": {
                "active_topic": {"text": topic, "trust": "inferred"},
                "open_questions": [{"text": t, "trust": "inferred"}
                                   for t in questions]},
            "epistemic_snapshot": {}}


def _write(sid, created, project=PROJECT, **kw):
    store.write_checkpoint(sid, _cp(sid, created, **kw), project_dir=project)


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT)


def _bucket():
    return config.checkpoint_dir() / SLUG


@pytest.fixture
def three(tmp_checkpoint_dir):
    _write("S-1", "2026-08-01T00:00:00Z", topic="first topic of the weekly sync",
           questions=[HIDE, KEEP])
    _write("S-2", "2026-08-02T00:00:00Z", topic="second topic of the weekly sync",
           questions=[HIDE, KEEP])
    _write("S-3", "2026-08-03T00:00:00Z", topic="third topic of the weekly sync",
           questions=[HIDE, KEEP])


# ---- pointers --------------------------------------------------------------


def test_the_pointer_window_is_latest_then_prev_in_numeric_order(three):
    for n in (10, 3):
        (_bucket() / f"prev-{n}.json").write_text(
            json.dumps(_cp(f"S-p{n}", "2026-07-01T00:00:00Z")))
    (_bucket() / "latest.json.bak-1").write_text("{}")
    (_bucket() / "events.jsonl").write_text("")
    got = view.pointers(PROJECT)
    assert [p.ref for p in got] == ["latest", "prev-1", "prev-2", "prev-3",
                                    "prev-10"]


def test_a_pointer_carries_its_envelope_and_the_judged_body(three):
    latest, prev1, _prev2 = view.pointers(PROJECT)
    assert latest.readable and latest.meta.session_id == "S-3"
    assert latest.meta.created == "2026-08-03T00:00:00Z"
    assert prev1.meta.session_id == "S-2"
    topic = latest.opened.checkpoint["working_context"]["active_topic"]
    assert topic["text"] == "third topic of the weekly sync"


def test_a_quarantined_item_is_removed_from_every_pointer(three):
    _quarantine(HIDE)
    got = view.pointers(PROJECT)
    assert len(got) == 3
    for p in got:
        texts = [q["text"] for q in
                 p.opened.checkpoint["working_context"]["open_questions"]]
        assert texts == [KEEP]
        assert [w.reason for w in p.opened.withheld] == ["quarantine"]
        assert HIDE not in json.dumps(p.opened.checkpoint)


def test_a_forgotten_topic_is_removed_and_the_envelope_stays(three):
    _forget("third topic of the weekly sync")
    latest = view.pointers(PROJECT)[0]
    assert "active_topic" not in latest.opened.checkpoint["working_context"]
    assert latest.meta.session_id == "S-3"


def test_an_unreadable_trust_ledger_closes_every_pointer(three):
    with open(_bucket() / "trust.jsonl", "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")
    got = view.pointers(PROJECT)
    assert all(p.opened.snapshot.closed for p in got)
    for p in got:
        wc = p.opened.checkpoint["working_context"]
        assert wc["open_questions"] == [] and "active_topic" not in wc
    assert any("trust.jsonl" in n for n in got[0].opened.snapshot.notes())


def test_a_torn_pointer_stays_listed_unreadable(three):
    (_bucket() / "prev-1.json").write_text("{not json")
    (_bucket() / "prev-2.json").write_text("[1, 2]")
    got = {p.ref: p for p in view.pointers(PROJECT)}
    for ref in ("prev-1", "prev-2"):
        assert got[ref].readable is False and got[ref].meta is None
        assert got[ref].opened.checkpoint is None
    assert got["latest"].readable


def test_no_bucket_has_no_pointers(tmp_checkpoint_dir):
    assert view.pointers(PROJECT) == ()
    assert view.pointers("") == ()


# ---- sessions --------------------------------------------------------------


def test_the_listing_is_this_projects_sessions_newest_first(three):
    _write("S-x", "2026-08-09T00:00:00Z", project="/p/other", topic="other")
    got = view.sessions(PROJECT)
    assert [(r.session_id, r.created, r.topic) for r in got.rows] == [
        ("S-3", "2026-08-03T00:00:00Z", "third topic of the weekly sync"),
        ("S-2", "2026-08-02T00:00:00Z", "second topic of the weekly sync"),
        ("S-1", "2026-08-01T00:00:00Z", "first topic of the weekly sync")]
    assert got.unreadable == 0 and got.notes == ()


def test_a_session_with_no_created_stamp_sorts_last_without_failing(three):
    cp = _cp("S-0", None)
    store.write_checkpoint("S-0", cp, project_dir=PROJECT)
    root = config.checkpoint_dir()
    raw = json.loads((root / "S-0.json").read_text())
    raw["created"] = 7
    (root / "S-0.json").write_text(json.dumps(raw))
    ids = [r.session_id for r in view.sessions(PROJECT).rows]
    assert ids[-1] == "S-0" and len(ids) == 4


def test_the_session_id_is_the_file_stem(three):
    root = config.checkpoint_dir()
    raw = json.loads((root / "S-1.json").read_text())
    raw["session_id"] = "payload-id"
    (root / "S-1.json").write_text(json.dumps(raw))
    assert "S-1" in [r.session_id for r in view.sessions(PROJECT).rows]


def test_a_withheld_topic_reads_as_absent(three):
    _forget("second topic of the weekly sync")
    _quarantine("third topic of the weekly sync", kind="topic")
    topics = {r.session_id: r.topic for r in view.sessions(PROJECT).rows}
    assert topics == {"S-3": None, "S-2": None, "S-1": "first topic of the weekly sync"}


def test_a_torn_session_file_is_counted_not_listed(three):
    root = config.checkpoint_dir()
    (root / "torn.json").write_text("{nope")
    (root / "listy.json").write_text("[1]")
    (root / "binary.json").write_bytes(b"\xff\xfe\x00")
    got = view.sessions(PROJECT)
    assert got.unreadable == 2
    assert [r.session_id for r in got.rows] == ["S-3", "S-2", "S-1"]


def test_under_tenant_scope_an_unattributable_file_is_not_counted(
        three, monkeypatch):
    """A torn file names no project, so counting it for a tenant-scoped
    caller would report activity in buckets it may not see."""
    (config.checkpoint_dir() / "torn.json").write_text("{nope")
    assert view.sessions(PROJECT).unreadable == 1
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    got = view.sessions(PROJECT)
    assert got.unreadable == 0 and len(got.rows) == 3


def test_an_unreadable_trust_ledger_hides_topics_and_says_why(three):
    with open(_bucket() / "trust.jsonl", "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")
    got = view.sessions(PROJECT)
    assert [r.topic for r in got.rows] == [None, None, None]
    assert any("trust.jsonl" in n for n in got.notes)


def test_a_missing_store_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "nope"))
    assert view.sessions(PROJECT) == view.Sessions((), 0, ())
    assert view.sessions("") == view.Sessions((), 0, ())


def test_the_listing_never_copies_or_filters_a_body(three, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the light listing opened a body")

    monkeypatch.setattr(view, "_filter", boom)
    monkeypatch.setattr(view, "_opened", boom)
    assert len(view.sessions(PROJECT).rows) == 3


# ---- open_sessions ---------------------------------------------------------


def test_open_sessions_judges_each_body_like_open(three):
    _quarantine(HIDE)
    got = view.open_sessions(PROJECT, ["S-1", "S-3"], live=False)
    assert set(got) == {"S-1", "S-3"}
    for opened in got.values():
        texts = [q["text"] for q in
                 opened.checkpoint["working_context"]["open_questions"]]
        assert texts == [KEEP]
        assert HIDE not in json.dumps(opened.checkpoint)
    assert got["S-1"].snapshot is got["S-3"].snapshot


def test_open_sessions_skips_what_it_may_not_open(three):
    root = config.checkpoint_dir()
    (root / "torn.json").write_text("{nope")
    (root / "other.json").write_text(json.dumps(
        _cp("other", "2026-08-01T00:00:00Z", project="/p/other")
        | {"project_slug": "-p-other"}))
    got = view.open_sessions(
        PROJECT, ["torn", "other", "S-9", "../S-1", "", "S-2"], live=False)
    assert set(got) == {"S-2"}


def test_open_sessions_live_drops_a_closed_loop(tmp_checkpoint_dir):
    cp = _cp("S-1", "2026-08-01T00:00:00Z", questions=[KEEP])
    cp["working_context"]["open_questions"][0]["id"] = "o-aaaaaaaaaaaa"
    store.write_checkpoint("S-1", cp, project_dir=PROJECT)
    store.append_event("o-aaaaaaaaaaaa", "resolved", project_dir=PROJECT)
    kept = view.open_sessions(PROJECT, ["S-1"], live=False)["S-1"]
    dropped = view.open_sessions(PROJECT, ["S-1"], live=True)["S-1"]
    assert (len(kept.checkpoint["working_context"]["open_questions"]),
            kept.suppressed) == (1, 0)
    assert (dropped.checkpoint["working_context"]["open_questions"],
            dropped.suppressed) == ([], 1)
