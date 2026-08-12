"""/api/ledger and /api/session (#670 slice 2): dispatch, slug discipline,
and sid validation happen at the server seam; the payload shapes are the
reader's and are covered by the reader tests."""
import json
import urllib.request

def _get_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())

def test_ledger_serves_the_reader_walk(srv_flat):
    data = _get_json(srv_flat + "/api/ledger?project=-tmp-proj")
    assert data["ok"] is True
    assert data["head"]["session_id"] == "rollout-2026-08-06-cccc"
    assert isinstance(data["groups"], list)
    assert data["totals"]["objects"] >= 0

def test_ledger_refuses_unknown_project(srv_flat):
    data = _get_json(srv_flat + "/api/ledger?project=..%2F..%2Fetc")
    assert data["ok"] is False
    assert "isn't one this inspector knows" in data["error"]["what"]

def test_session_serves_one_sessions_events(srv_flat):
    data = _get_json(srv_flat + "/api/session?project=-tmp-proj&sid=bbbb-2222")
    assert data["ok"] is True
    assert data["session"]["session_id"] == "bbbb-2222"
    assert data["session"]["active_topic"] == "day two"
    assert "objects" in data and "counts" in data

def test_session_requires_a_sid(srv_flat):
    data = _get_json(srv_flat + "/api/session?project=-tmp-proj")
    assert data["ok"] is False

def test_session_refuses_a_sid_outside_history(srv_flat):
    data = _get_json(srv_flat + "/api/session?project=-tmp-proj&sid=..%2Fother")
    assert data["ok"] is False
    assert "isn't part of" in data["error"]["what"]
