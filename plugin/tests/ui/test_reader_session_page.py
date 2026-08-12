"""session_events: what one session wrote (#670 slice 2 session page).

The page renders only events attributable to the named session — first seen,
changed, last seen transitions from the walk. Resolutions and quote checks
carry no session attribution in their ledgers and must NOT be claimed by a
session page; inventing attribution would be the viewer asserting something
the stored data does not.
"""
import json
import pytest
from daimon_ui import reader

from tests.ui.test_reader_ledger import _cp, _item  # same history shapes

@pytest.fixture
def session_history(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / "s1-aaaa.json").write_text(json.dumps(_cp(
        "s1-aaaa", "2026-08-04T10:00:00Z", slug,
        [_item("o-aaa111aaa111", "build gates on bundle", "inferred"),
         _item("o-bbb222bbb222", "old question")])))
    (d / "s2-bbbb.json").write_text(json.dumps(_cp(
        "s2-bbbb", "2026-08-05T10:00:00Z", slug,
        [_item("o-aaa111aaa111", "build gates on bundle", "verbatim"),
         _item("o-ccc333ccc333", "token lifetime rewritten", "verbatim")],
        topic="day two")))
    (d / slug / "latest.json").write_text(json.dumps(_cp(
        "s2-bbbb", "2026-08-05T10:00:00Z", slug, [])))
    return d, slug

def test_session_page_reports_this_sessions_transitions(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "s2-bbbb")
    assert result["ok"] is True
    assert result["session"]["session_id"] == "s2-bbbb"
    assert result["session"]["created"] == "2026-08-05T10:00:00Z"
    assert result["session"]["active_topic"] == "day two"
    by_id = {o["id"]: o for o in result["objects"]}
    assert by_id["o-aaa111aaa111"]["events"][0]["kind"] == "changed"
    assert by_id["o-ccc333ccc333"]["events"][0]["kind"] == "first_seen"
    # o-bbb vanished at the s2 transition: last seen, ts = its final sighting (s1)
    assert by_id["o-bbb222bbb222"]["events"][0]["kind"] == "last_seen"
    assert by_id["o-bbb222bbb222"]["events"][0]["ts"] == "2026-08-04T10:00:00Z"
    assert result["counts"] == {"first_seen": 1, "changed": 1, "last_seen": 1}

def test_oldest_session_births_everything_it_holds(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "s1-aaaa")
    kinds = {o["id"]: o["events"][0]["kind"] for o in result["objects"]}
    assert kinds == {"o-aaa111aaa111": "first_seen", "o-bbb222bbb222": "first_seen"}

def test_unknown_session_id_is_refused_before_any_path_is_built(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "../../etc/passwd")
    assert result["ok"] is False
    assert "isn't part of" in result["error"]["what"]

def test_session_objects_carry_the_recall_kind_word(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "s2-bbbb")
    kinds = {o["id"]: o["kind"] for o in result["objects"]}
    assert set(kinds.values()) == {"question"}  # fixture items are open_questions

def test_session_torn_between_validation_and_read_reports_the_error(session_history, monkeypatch):
    """sid passes the history exact-match, then the file tears before the page's
    own read: the reader must hand back the load error, not crash or invent."""
    d, slug = session_history

    def gone(data_dir, sid):
        return None, {"what": f"Session {sid} doesn't exist.",
                      "why": "No checkpoint file was found for that session.",
                      "fix": "Pick a session from the project's history."}

    monkeypatch.setattr(reader, "_load_session", gone)
    result = reader.session_events(d, slug, "s2-bbbb")
    assert result["ok"] is False
    assert "doesn't exist" in result["error"]["what"]

def test_changed_event_names_the_fields(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "s2-bbbb")
    by_id = {o["id"]: o for o in result["objects"]}
    assert by_id["o-aaa111aaa111"]["events"][0]["detail"] == "trust"

def test_receipt_rides_along_only_when_the_project_opted_in(session_history):
    d, slug = session_history
    result = reader.session_events(d, slug, "s2-bbbb")
    assert "receipt" not in result["session"]  # no opt-in: silence, not nagging
    # opt in: latest.json claims receipts, session file claims one too
    marked = _cp("s2-bbbb", "2026-08-05T10:00:00Z", slug, [])
    marked["receipts"] = True
    (d / slug / "latest.json").write_text(json.dumps(marked))
    session = json.loads((d / "s2-bbbb.json").read_text())
    session["receipts"] = True
    (d / "s2-bbbb.json").write_text(json.dumps(session))
    result = reader.session_events(d, slug, "s2-bbbb")
    assert result["session"]["receipt"]["state"] in ("match", "mismatch", "missing")
