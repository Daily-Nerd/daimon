"""The viewer's pointer and session routes read through the view (#1132 PR 8b-1).

`/api/projects`, `/api/checkpoints`, `/api/checkpoint/<ref>`, `/api/history`
and `/api/diff` show what a reader of the project may see: a quarantined or
forgotten value is not in any payload, counts are counts of visible items, and
a trust ledger that cannot be read hides items and says so in `notes`. Stores
are built by the real writers and served by the real server."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from daimon_briefing import normalize, store, trust
from daimon_ui import server
from tests.ui.scope import scoped

PROJECT = "/p/viewer"
SLUG = store.project_slug(PROJECT)
HIDE = "the plan that was never reviewed by anybody"
KEEP = "an open question that stays visible to all"
CLOSE = "a goal that gets closed from the viewer"
TOPIC = "the weekly sync cadence for the viewer"


def _cp(sid, created, *, topic=TOPIC, questions=(), decisions=()):
    return {"session_id": sid, "created": created, "author": "ada",
            "format_version": "D-019",
            "working_context": {
                "active_topic": {"text": topic, "trust": "inferred"},
                "open_questions": list(questions),
                "recent_decisions": list(decisions)},
            "epistemic_snapshot": {}}


def _q(text, iid):
    return {"text": text, "id": iid, "trust": "inferred"}


def _write(sid, created, **kw):
    store.write_checkpoint(sid, _cp(sid, created, **kw), project_dir=PROJECT)


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT)


def _break_trust(tmp_checkpoint_dir):
    with open(tmp_checkpoint_dir / SLUG / "trust.jsonl", "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")


@pytest.fixture
def two(tmp_checkpoint_dir):
    _write("S-1", "2026-08-01T00:00:00Z", topic="first topic of the viewer",
           questions=[_q(CLOSE, "o-aaaaaaaaaaaa"), _q(KEEP, "o-bbbbbbbbbbbb"),
                      _q(HIDE, "o-dddddddddddd")])
    _write("S-2", "2026-08-02T00:00:00Z", topic=TOPIC,
           questions=[_q(KEEP, "o-bbbbbbbbbbbb"), _q(HIDE, "o-dddddddddddd")])
    return tmp_checkpoint_dir


@pytest.fixture
def base(two):
    s = server.make_server(two, SLUG, "viewer", port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path) as resp:
        return json.loads(resp.read())


# ---- /api/projects ---------------------------------------------------------


def test_projects_counts_visible_items_only(base):
    _quarantine(HIDE)
    [row] = _get(base, "/api/projects")["projects"]
    assert row["slug"] == SLUG and row["item_count"] == 1
    assert row["active_topic"] == TOPIC


def test_projects_hides_a_withheld_topic(base):
    _forget(TOPIC)
    [row] = _get(base, "/api/projects")["projects"]
    assert row["active_topic"] is None
    assert TOPIC not in json.dumps(_get(base, "/api/projects"))


def test_projects_sorts_newest_first_with_a_torn_bucket_last(base, two):
    torn = two / "-p-torn"
    torn.mkdir()
    (torn / "latest.json").write_text("{not json")
    rows = _get(base, "/api/projects")["projects"]
    assert [r["slug"] for r in rows] == [SLUG, "-p-torn"]
    assert rows[1]["item_count"] is None and rows[1]["created"] is None


# ---- /api/checkpoints and /api/checkpoint/<ref> ----------------------------


def test_checkpoints_lists_the_window_with_visible_topics(base):
    _forget("first topic of the viewer")
    got = _get(base, "/api/checkpoints")
    assert [(c["ref"], c["active_topic"]) for c in got["checkpoints"]] == [
        ("latest", TOPIC), ("prev-1", None)]
    assert got["sessions_total"] == 2 and got["notes"] == []
    assert "first topic of the viewer" not in json.dumps(got)


def test_a_checkpoint_never_shows_a_quarantined_or_forgotten_item(base):
    _quarantine(HIDE)
    _forget(KEEP)
    for ref in ("latest", "prev-1"):
        got = _get(base, f"/api/checkpoint/{ref}")
        assert got["ok"] is True
        blob = json.dumps(got)
        assert HIDE not in blob and KEEP not in blob


def test_a_checkpoint_keeps_its_envelope_and_a_visible_item(base):
    _quarantine(HIDE)
    got = _get(base, "/api/checkpoint/latest")
    assert got["meta"]["session_id"] == "S-2"
    assert got["meta"]["active_topic"] == TOPIC
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert texts == [KEEP] and got["notes"] == []


def test_a_checkpoint_errors_keep_their_shape(base, two):
    missing = _get(base, "/api/checkpoint/prev-7")
    assert missing["ok"] is False and "doesn't exist" in missing["error"]["what"]
    (two / SLUG / "prev-1.json").write_text("{not json")
    torn = _get(base, "/api/checkpoint/prev-1")
    assert torn["ok"] is False and "Couldn't read" in torn["error"]["what"]
    bad = _get(base, "/api/checkpoint/S-1")
    assert bad["ok"] is False and "isn't one this inspector serves" in (
        bad["error"]["what"])


def test_an_unreadable_trust_ledger_hides_items_and_says_why(base, two):
    _break_trust(two)
    got = _get(base, "/api/checkpoint/latest")
    assert got["ok"] is True
    assert [i for s in got["sections"] for i in s["items"]] == []
    assert got["meta"]["active_topic"] is None
    assert any("trust.jsonl" in n for n in got["notes"])
    listing = _get(base, "/api/checkpoints")
    assert [c["active_topic"] for c in listing["checkpoints"]] == [None, None]
    assert any("trust.jsonl" in n for n in listing["notes"])


def test_a_receipt_state_rides_a_receipts_checkpoint(two):
    (two / SLUG / "latest.json").write_text(json.dumps(
        _cp("S-2", "2026-08-02T00:00:00Z") | {"receipts": True}))
    got = scoped(two).load_checkpoint(SLUG, "latest")
    assert got["meta"]["receipt"]["state"] == "missing"


# ---- /api/history ----------------------------------------------------------


def test_history_lists_visible_topics_and_counts_torn_files(base, two):
    _forget("first topic of the viewer")
    (two / "torn.json").write_text("{nope")
    got = _get(base, "/api/history")
    assert [(s["session_id"], s["active_topic"]) for s in got["sessions"]] == [
        ("S-2", TOPIC), ("S-1", None)]
    assert got["unreadable"] == 1 and got["project"] == "viewer"
    assert got["notes"] == []


def test_history_says_why_when_the_trust_ledger_is_unreadable(base, two):
    _break_trust(two)
    got = _get(base, "/api/history")
    assert [s["active_topic"] for s in got["sessions"]] == [None, None]
    assert any("trust.jsonl" in n for n in got["notes"])


# ---- /api/diff -------------------------------------------------------------


def _diff(base, a="S-1", b="S-2"):
    return _get(base, f"/api/diff?a={a}&b={b}")


def test_diff_never_shows_a_withheld_item(base):
    _quarantine(HIDE)
    got = _diff(base)
    assert got["ok"] is True
    assert HIDE not in json.dumps(got)
    assert [i["id"] for i in got["gone"]] == ["o-aaaaaaaaaaaa"]
    assert [i["item"]["id"] for i in got["carried"]] == ["o-bbbbbbbbbbbb"]


@pytest.mark.parametrize("status", ["resolved", "resolved-agent-verified",
                                    "done", "superseded"])
def test_diff_shows_an_item_closed_by_any_resolving_status_as_resolved(
        base, status):
    store.append_event("o-aaaaaaaaaaaa", status, note="closed by the owner",
                       project_dir=PROJECT)
    got = _diff(base)
    assert [r["item"]["id"] for r in got["resolved"]] == ["o-aaaaaaaaaaaa"]
    assert got["resolved"][0]["note"] == "closed by the owner"
    assert got["gone"] == []


def test_diff_keeps_a_reopened_item_gone_not_resolved(base):
    store.append_event("o-aaaaaaaaaaaa", "resolved", project_dir=PROJECT)
    store.append_event("o-aaaaaaaaaaaa", "reopened", project_dir=PROJECT)
    got = _diff(base)
    assert got["resolved"] == []
    assert [i["id"] for i in got["gone"]] == ["o-aaaaaaaaaaaa"]


def test_a_quarantined_resolution_note_reads_as_the_marker(base):
    store.append_event("o-aaaaaaaaaaaa", "resolved", note=HIDE,
                       project_dir=PROJECT)
    rec = _quarantine(HIDE)
    got = _diff(base)
    assert [r["note"] for r in got["resolved"]] == [
        f"[withheld: quarantine {rec}]"]
    assert HIDE not in json.dumps(got)


def test_a_forgotten_resolution_note_reads_as_absent(base):
    store.append_event("o-aaaaaaaaaaaa", "resolved", note=HIDE,
                       project_dir=PROJECT)
    _forget(HIDE)
    got = _diff(base)
    assert [r["note"] for r in got["resolved"]] == [None]
    assert HIDE not in json.dumps(got)


def test_the_default_pair_is_the_two_newest_sessions(base):
    got = _get(base, "/api/diff")
    assert got["a"]["session_id"] == "S-1" and got["b"]["session_id"] == "S-2"


def test_a_single_session_has_no_diff(tmp_checkpoint_dir):
    _write("S-1", "2026-08-01T00:00:00Z")
    s = server.make_server(tmp_checkpoint_dir, SLUG, "viewer", port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        got = _get(f"http://127.0.0.1:{s.server_address[1]}", "/api/diff")
    finally:
        s.shutdown()
        s.server_close()
    assert got == {"ok": True, "empty": "single_checkpoint", "sessions": 1}


def test_diff_errors_name_the_session(base):
    bad = _diff(base, a="nope")
    assert bad["ok"] is False and "'nope'" in bad["error"]["what"]
    esc = _diff(base, b="../../etc/passwd")
    assert esc["ok"] is False


@pytest.mark.parametrize("missing", ["S-1", "S-2"])
def test_diff_error_when_a_listed_session_vanishes(two, monkeypatch, missing):
    """A session can vanish between the listing and the open (GC race): either
    side surfaces the error rather than diffing nothing."""
    from daimon_briefing import view
    real = view.open_sessions

    def without(project, sids, *, live):
        got = real(project, sids, live=live)
        got.pop(missing)
        return got

    monkeypatch.setattr(view, "open_sessions", without)
    got = scoped(two).diff_checkpoints(SLUG, "S-1", "S-2")
    assert got["ok"] is False
    assert got["error"]["what"] == f"Session {missing} doesn't exist."


def test_an_unreadable_trust_ledger_empties_the_diff_and_says_why(base, two):
    _break_trust(two)
    got = _diff(base)
    assert got["born"] == got["gone"] == got["carried"] == []
    assert any("trust.jsonl" in n for n in got["notes"])


# ---- one failure shape -----------------------------------------------------


@pytest.mark.parametrize("name, path", [
    ("projects", "/api/projects"), ("pointers", "/api/checkpoints"),
    ("pointers", "/api/checkpoint/latest"), ("sessions", "/api/history"),
    ("sessions", "/api/diff"), ("open_sessions", "/api/diff?a=S-1&b=S-2")])
def test_a_view_that_raises_is_a_generic_500(base, monkeypatch, name, path):
    from daimon_briefing import view

    def boom(*_a, **_k):
        raise RuntimeError(f"{HIDE} at /private/path")

    monkeypatch.setattr(view, name, boom)
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + path)
    assert err.value.code == 500
    body = err.value.read()
    assert HIDE.encode() not in body and b"/private" not in body
