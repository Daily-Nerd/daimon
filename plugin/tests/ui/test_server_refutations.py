"""Refutations lane (#670 slice 3): /api/refutations dispatches to the
refutations engine — the lane renders `daimon refute list`, it never grows a
second fold. Row order is the CLI's own (active first, then updated_at, then
refutation_id), owned by refutations.listing() so the two surfaces cannot
drift apart."""
import json
import threading
from urllib.request import urlopen

import pytest

from daimon_briefing import refutations, store
from daimon_ui import server


def _cp(sid):
    return {
        "session_id": sid,
        "working_context": {
            "active_topic": {"text": "wiring the lane"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


@pytest.fixture
def refut_proj(tmp_checkpoint_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    proj = tmp_path / "proj"
    proj.mkdir()
    store.write_checkpoint("S1", _cp("S1"), project_dir=proj)
    return proj


@pytest.fixture
def refut_srv(refut_proj, tmp_checkpoint_dir):
    slug = store.project_slug(refut_proj)
    s = server.make_server(tmp_checkpoint_dir, slug, refut_proj.name, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _get(base, path):
    with urlopen(base + path) as r:
        return json.loads(r.read())


def test_listing_is_the_cli_order(refut_proj):
    """active sorts before candidate regardless of recency — the shared
    listing() carries the exact sort _cmd_refute_list used inline."""
    cand = refutations.assert_refutation(
        subject="the newer candidate", verdict="does not hold",
        scope="tests", evidence=["artifact:a"], channel="cli-agent",
        project_dir=refut_proj)
    active = refutations.assert_refutation(
        subject="the older active", verdict="does not hold",
        scope="tests", evidence=["artifact:b"], channel="cli-tty",
        ratified=True, project_dir=refut_proj)
    rows = refutations.listing(project_dir=refut_proj)
    assert [r["refutation_id"] for r in rows] == [active, cand]
    assert rows[0]["state"] == "active"


def test_listing_filters_states(refut_proj):
    refutations.assert_refutation(
        subject="a candidate", verdict="does not hold", scope="tests",
        evidence=["artifact:a"], channel="cli-agent", project_dir=refut_proj)
    assert refutations.listing(states={"active"}, project_dir=refut_proj) == []


def test_endpoint_rows_are_the_engines(refut_srv, refut_proj):
    rid = refutations.assert_refutation(
        subject="feature flags reload live", verdict="does not hold",
        scope="the boot path", evidence=["artifact:src/boot.ts"],
        anchors=["o-6ba3d7e51f08"], channel="cli-agent",
        project_dir=refut_proj)
    out = _get(refut_srv, "/api/refutations")
    assert out["ok"] is True
    row = out["rows"][0]
    # engine fields pass through untouched — no viewer reshaping
    assert row["refutation_id"] == rid
    assert row["subject"] == "feature flags reload live"
    assert row["state"] == "candidate"
    assert row["asserted_by"] == "agent"
    assert row["anchors"] == ["o-6ba3d7e51f08"]


def test_endpoint_empty_ledger_is_ok_and_empty(refut_srv):
    out = _get(refut_srv, "/api/refutations")
    assert out["ok"] is True
    assert out["rows"] == []


def test_endpoint_rejects_unknown_slug(refut_srv):
    out = _get(refut_srv, "/api/refutations?project=not-a-project")
    assert out["ok"] is False
    assert set(out["error"]) == {"what", "why", "fix"}
