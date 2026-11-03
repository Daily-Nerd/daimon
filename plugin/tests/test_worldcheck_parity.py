"""#1279: every briefing render path opts in to worldcheck the same way.

The class of bug: `briefing.prepare` only runs `worldcheck.check` when a
caller passes `worldcheck_project`, so a path that forgets the argument
silently never shows world evidence. These tests enumerate the four entry
points (the CLI brief, the MCP `daimon_brief` tool, the host API
`render_briefing` and the Hermes `pre_llm_call` hook) so a fifth path, or a
regression in one of the four, fails here."""
import json

import pytest

import daimon_briefing as plugin  # noqa: F401 - package import side effects
from daimon_briefing import (api, cli, config, hooks, mcp_server, store,
                             worldcheck)
from daimon_briefing.surfaces import Writer

CLAIM = "the fix lives in src/fix.py"


def _checkpoint(texts=(CLAIM,)):
    return {
        "session_id": "S-now",
        "working_context": {"open_questions": [
            {"text": t, "trust": "inferred", "carried_from": "S-prev",
             "id": f"o-{i + 1:06x}"} for i, t in enumerate(texts)]},
        "epistemic_snapshot": {},
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A real project directory with one carried claim, as the session's own
    project (resolved: the paths below are compared for equality)."""
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    store.write_checkpoint("S-now", _checkpoint(), project_dir=str(root),
                           writer=Writer.HUMAN)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(root))
    # No `gh`: the file claim above is the only probe, a disk read.
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    return str(root)


def _run_cli(project):
    assert cli.main(["brief"]) == 0


def _run_mcp(project):
    got = mcp_server._handle_tools_call({"name": "daimon_brief",
                                         "arguments": {}})
    assert got["isError"] is False


def _run_api(project):
    assert api.render_briefing(project, now=1_800_000_000.0).state != \
        "unavailable"


def _run_hermes(project):
    out = hooks.pre_llm_call(session_id="S2", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert out is not None


ENTRY_POINTS = {"cli": _run_cli, "mcp": _run_mcp, "api": _run_api,
                "hermes": _run_hermes}


@pytest.fixture
def recorder(monkeypatch):
    calls: list = []

    def fake_check(checkpoint, project_dir, *, skip_reasons=None):
        calls.append(project_dir)
        return {}

    monkeypatch.setattr(worldcheck, "check", fake_check)
    return calls


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_flag_on_every_entry_point_runs_check_once(
        project, recorder, monkeypatch, capsys, name):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    ENTRY_POINTS[name](project)
    assert recorder == [project]


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_flag_unset_no_entry_point_runs_check(
        project, recorder, monkeypatch, capsys, name):
    """The default stays off (D5): none of the four paths probes."""
    monkeypatch.delenv("DAIMON_WORLDCHECK", raising=False)
    ENTRY_POINTS[name](project)
    assert recorder == []


def test_mcp_read_by_slug_never_probes_even_with_the_grant(
        project, recorder, monkeypatch):
    """A slug names a bucket, not a path: `gh` would answer for the cwd repo."""
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    other = "/repo/other-tenant"
    store.write_checkpoint("S-other", _checkpoint(), project_dir=other,
                           writer=Writer.HUMAN)
    for slug in (store.project_slug(other), store.project_slug(project)):
        got = mcp_server._handle_tools_call(
            {"name": "daimon_brief", "arguments": {"slug": slug}})
        assert got["isError"] is False
    assert recorder == []


def _verification_rows(project):
    path = (config.checkpoint_dir() / store.project_slug(project)
            / "verification.jsonl")
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_mcp_brief_writes_the_ledger_row_for_a_contradiction(
        project, monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    got = mcp_server._handle_tools_call({"name": "daimon_brief",
                                         "arguments": {}})
    assert got["isError"] is False
    assert "state changed since capture: src/fix.py missing" in \
        got["content"][0]["text"]
    rows = _verification_rows(project)
    assert [(r["check"], r["reason"]) for r in rows] == [
        ("file-exists", "claim-contradicted")]
    assert rows[0]["item_ref"] == "o-000001"


def test_api_render_briefing_writes_the_ledger_row_and_never_raises(
        project, monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    got = api.render_briefing(project, now=1_800_000_000.0)
    assert "state changed since capture: src/fix.py missing" in got.text
    assert len(_verification_rows(project)) == 1

    def boom(*a, **k):
        raise RuntimeError("commit failed")

    monkeypatch.setattr("daimon_briefing.effects_commit.commit", boom)
    again = api.render_briefing(project, now=1_800_000_000.0)
    assert again.state == "briefing"
    assert again.text == got.text
