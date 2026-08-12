"""Engine endpoints (#670): /api/recall and /api/why dispatch to daimon_briefing's
own engines. recall.search is THE matcher (one-matcher rule: the viewer renders
recall, it never grows a second search engine) and inspect_item is the read-side
receipt behind `daimon why`. reader.py stays daimon-import-free; the engine
imports live in the server dispatch layer only.
"""
import json
import threading
from urllib.request import urlopen

import pytest

from daimon_briefing import store
from daimon_ui import server


def _engine_cp(sid):
    return {
        "session_id": sid,
        "working_context": {
            "active_topic": {"text": "wiring the viewer", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": "Adopt sqlite for the recall index",
                 "trust": "verbatim", "id": "d-0a1b2c3d4e5f",
                 "quote": "let's adopt sqlite for the recall index"},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


@pytest.fixture
def engine_srv(tmp_checkpoint_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    proj = tmp_path / "proj"
    proj.mkdir()
    store.write_checkpoint("S1", _engine_cp("S1"), project_dir=proj)
    slug = store.project_slug(proj)
    s = server.make_server(tmp_checkpoint_dir, slug, proj.name, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _get(base, path):
    with urlopen(base + path) as r:
        return json.loads(r.read())


# ---- /api/recall ----

def test_recall_returns_matcher_rows(engine_srv):
    out = _get(engine_srv, "/api/recall?q=sqlite")
    assert out["ok"] is True
    texts = [r["text"] for r in out["rows"]]
    assert "Adopt sqlite for the recall index" in texts
    row = out["rows"][texts.index("Adopt sqlite for the recall index")]
    # the row is recall.search's row, passed through — not a viewer reshaping
    assert row["item_id"] == "d-0a1b2c3d4e5f"
    assert row["trust"] == "verbatim"
    assert row["session_id"] == "S1"


def test_recall_respects_limit(engine_srv):
    out = _get(engine_srv, "/api/recall?q=sqlite&limit=1")
    assert out["ok"] is True
    assert len(out["rows"]) <= 1


def test_recall_missing_query_is_an_error_shape(engine_srv):
    out = _get(engine_srv, "/api/recall")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_recall_bad_limit_is_an_error_shape(engine_srv):
    out = _get(engine_srv, "/api/recall?q=sqlite&limit=zero")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_recall_no_matches_is_ok_and_empty(engine_srv):
    out = _get(engine_srv, "/api/recall?q=zzzznothing")
    assert out["ok"] is True
    assert out["rows"] == []


# ---- /api/why ----

def test_why_returns_inspect_item_payload(engine_srv):
    out = _get(engine_srv, "/api/why?id=d-0a1b2c3d4e5f")
    assert out["ok"] is True
    # the payload is inspect_item's, passed through: item + axes + corroboration
    assert out["item"]["item_id"] == "d-0a1b2c3d4e5f"
    assert "axes" in out and "corroboration" in out


def test_why_invalid_id_shape_is_an_error(engine_srv):
    out = _get(engine_srv, "/api/why?id=not-an-id!")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_why_unknown_id_is_an_error(engine_srv):
    out = _get(engine_srv, "/api/why?id=d-ffffffffffff")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_why_missing_id_is_an_error(engine_srv):
    out = _get(engine_srv, "/api/why")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_recall_rejects_unknown_project_slug(engine_srv):
    out = _get(engine_srv, "/api/recall?project=-nope&q=sqlite")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_recall_whitespace_query_is_an_error(engine_srv):
    out = _get(engine_srv, "/api/recall?q=%20%20")
    assert out["ok"] is False


def test_recall_engine_failure_is_an_error_shape(engine_srv, monkeypatch):
    from daimon_briefing import recall as recall_mod

    def boom(*a, **k):
        raise recall_mod.RecallError("no FTS5")

    monkeypatch.setattr(recall_mod, "search", boom)
    out = _get(engine_srv, "/api/recall?q=sqlite")
    assert out["ok"] is False
    assert "FTS5" in out["error"]["fix"]


def test_why_rejects_unknown_project_slug(engine_srv):
    out = _get(engine_srv, "/api/why?project=-nope&id=d-0a1b2c3d4e5f")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}


def test_recall_zero_limit_is_an_error(engine_srv):
    out = _get(engine_srv, "/api/recall?q=sqlite&limit=0")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}
