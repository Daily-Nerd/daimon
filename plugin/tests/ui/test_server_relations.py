"""/api/relations — the Phase 3 History lane's engine endpoint (#678).

Confirmed edges only, erased chains withheld, engine fields passed through
untouched. Candidates and rejections never reach this surface: a chain a
reader sees is always one a human vouched for.
"""

import json
import threading
from urllib.request import urlopen

import pytest

from daimon_briefing import relations, store
from daimon_ui import server

PROJECT_NAME = "relations-lane-arc"


def _cp(sid):
    return {
        "session_id": sid,
        "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [
                {"text": "keep the history lane confirmed only",
                 "trust": "inferred"},
                {"text": "resolve endpoint texts at read time",
                 "trust": "inferred"}]},
    }


@pytest.fixture
def rel_proj(tmp_checkpoint_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    proj = tmp_path / PROJECT_NAME
    proj.mkdir()
    store.write_checkpoint("S1", _cp("S1"), project_dir=proj)
    stored = store.read_latest(project_dir=proj, fallback=False)
    ids = [i["id"] for i in stored["working_context"]["recent_decisions"]]
    confirmed = relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S1", "field": "recent_decisions",
                       "item_id": ids[0]},
        to_endpoint={"session_id": "S1", "field": "recent_decisions",
                     "item_id": ids[1]},
        matched_by=["carry-absolute"], matcher_version="lab-2026-08-12",
        channel="lab-import", project_dir=proj)
    # `ui` is an in-process human channel: no tty observation at the module
    # seam (that check is the CLI's), which is exactly how a future in-process
    # confirmer would write.
    relations.confirm(confirmed, channel="ui", project_dir=proj)
    # a candidate that must never reach the wire
    relations.propose(
        type_="answers",
        from_endpoint={"session_id": "S1", "field": "recent_decisions",
                       "item_id": ids[0]},
        to_endpoint={"session_id": "S1", "field": "open_questions",
                     "item_id": "r-feedbeef1234"},
        matched_by=["carry-absolute"], matcher_version="lab-2026-08-12",
        channel="lab-import", project_dir=proj)
    return {"proj": proj, "ids": ids, "confirmed": confirmed}


@pytest.fixture
def rel_srv(rel_proj, tmp_checkpoint_dir):
    slug = store.project_slug(rel_proj["proj"])
    s = server.make_server(tmp_checkpoint_dir, slug, PROJECT_NAME, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _get(base, path):
    with urlopen(base + path) as r:
        return json.loads(r.read())


def test_confirmed_edges_pass_through_with_texts(rel_proj, rel_srv):
    out = _get(rel_srv, f"/api/relations?id={rel_proj['ids'][0]}")
    assert out["ok"] is True
    assert [r["relation_id"] for r in out["rows"]] == [rel_proj["confirmed"]]
    row = out["rows"][0]
    assert row["state"] == "confirmed"
    assert row["type"] == "revision-of"
    assert row["from"]["item_id"] == rel_proj["ids"][0]
    assert out["texts"][rel_proj["ids"][1]] == \
        "resolve endpoint texts at read time"
    assert out["withheld"] == 0


def test_candidates_never_reach_the_wire(rel_proj, rel_srv):
    out = _get(rel_srv, f"/api/relations?id={rel_proj['ids'][0]}")
    assert all(r["state"] == "confirmed" for r in out["rows"])
    assert len(out["rows"]) == 1


def test_erased_chains_are_withheld_with_a_safe_count(rel_proj, rel_srv):
    store.append_event(rel_proj["ids"][1], "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=rel_proj["proj"])
    out = _get(rel_srv, f"/api/relations?id={rel_proj['ids'][0]}")
    assert out["rows"] == []
    assert out["withheld"] == 1
    assert rel_proj["ids"][1] not in json.dumps(out)


def test_unknown_slug_yields_the_three_key_error(rel_srv):
    out = _get(rel_srv, "/api/relations?id=r-feedbeef1234&project=nope")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}
