"""Rulings lane (#693 PR 2): /api/refutations serves BOTH lanes in one
payload — `rows` stays refutation-polarity (the ✗/"Refutes" vocabulary
would invert a ruling), `rulings` carries the ruling-polarity records for
the lane that renders VERDICT under its own § glyph. Every listing call is
polarity-scoped explicitly: an unscoped call is the exact bug shape the
polarity parameter exists to prevent."""
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
def proj(tmp_checkpoint_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    p = tmp_path / "proj"
    p.mkdir()
    store.write_checkpoint("S1", _cp("S1"), project_dir=p)
    return p


@pytest.fixture
def srv(proj, tmp_checkpoint_dir):
    slug = store.project_slug(proj)
    s = server.make_server(tmp_checkpoint_dir, slug, proj.name, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _get(base, path):
    with urlopen(base + path) as r:
        return json.loads(r.read())


def _seed_both(proj):
    ruling = refutations.assert_ruling(
        subject="friday deploys", verdict="never deploy on friday",
        scope="payments", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=proj)
    refut = refutations.assert_refutation(
        subject="the polling approach", verdict="does not hold",
        scope="tests", evidence=["artifact:a"], channel="cli-agent",
        project_dir=proj)
    return ruling, refut


def test_payload_separates_the_two_polarities(proj, srv):
    ruling, refut = _seed_both(proj)
    slug = store.project_slug(proj)
    data = _get(srv, f"/api/refutations?project={slug}")
    assert data["ok"] is True
    assert [r["refutation_id"] for r in data["rows"]] == [refut]
    assert [r["refutation_id"] for r in data["rulings"]] == [ruling]


def test_rulings_lane_order_is_the_cli_order(proj, srv):
    cand = refutations.assert_ruling(
        subject="newer candidate rule", verdict="candidate verdict",
        scope="tests", evidence=["issue:693"], channel="cli-agent",
        project_dir=proj)
    active = refutations.assert_ruling(
        subject="older active rule", verdict="active verdict",
        scope="tests", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=proj)
    slug = store.project_slug(proj)
    data = _get(srv, f"/api/refutations?project={slug}")
    assert [r["refutation_id"] for r in data["rulings"]] == [active, cand]
