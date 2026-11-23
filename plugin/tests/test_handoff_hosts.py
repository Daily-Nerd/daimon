"""Every briefing host shows the baton, labelled by its tier, by construction.

The baton used to be read in one host's entry (`cli/brief.py`), outside the
preparation every host shares, so the MCP tool, `api.render_briefing` and the
Hermes hook never showed it. `briefing.prepare` now returns it and the hosts
only render it.
"""
import ast
from pathlib import Path

import pytest

from daimon_briefing import (api, briefing, cli, hooks, marks, mcp_tools,
                             serializer, store)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/handoff-hosts"
BATON = "pick up the migration"
HUMAN_TITLE = "HANDOFF (left deliberately by previous session, "
AGENT_TITLE = "HANDOFF (written by an agent, unverified, "
PACKAGE = Path(store.__file__).parent


def _cp():
    return {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": "an unrelated decision", "trust": "inferred"}]},
            "epistemic_snapshot": {}}


def _seed(checkpoint=True):
    if checkpoint:
        store.write_checkpoint("S-1", _cp(), project_dir=PROJECT,
                               writer=Writer.HUMAN)


def _human_baton():
    assert store.append_event("", "active", note=BATON, kind="handoff",
                              project_dir=PROJECT, source="cli-tty",
                              writer=Writer.HUMAN)


def _cli(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


def _mcp(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    return mcp_tools.HANDLERS["daimon_brief"]({"project": PROJECT}).text


def _api(capsys, monkeypatch):
    return api.render_briefing(PROJECT).text


def _hermes(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    return out["context"]


HOSTS = [_cli, _mcp, _api, _hermes]
IDS = ["cli", "mcp", "api", "hermes"]


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
@pytest.mark.parametrize("checkpoint", [True, False])
def test_an_agent_baton_shows_in_every_host_with_the_agent_label(
        tmp_checkpoint_dir, monkeypatch, capsys, host, checkpoint):
    _seed(checkpoint)
    api.write_handoff(PROJECT, BATON)
    out = host(capsys, monkeypatch)
    assert AGENT_TITLE in out and HUMAN_TITLE not in out
    assert f"→ {BATON}" in out


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_a_human_baton_shows_in_every_host_with_the_human_label(
        tmp_checkpoint_dir, monkeypatch, capsys, host):
    _seed()
    _human_baton()
    out = host(capsys, monkeypatch)
    assert HUMAN_TITLE in out and AGENT_TITLE not in out
    assert f"→ {BATON}" in out


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_no_baton_changes_nothing(tmp_checkpoint_dir, monkeypatch, capsys,
                                  host):
    _seed()
    assert "HANDOFF" not in host(capsys, monkeypatch)


def test_the_machine_brief_leads_with_the_baton(tmp_checkpoint_dir,
                                                monkeypatch):
    _seed()
    api.write_handoff(PROJECT, BATON)
    text = api.render_briefing(PROJECT).text
    assert text.startswith(AGENT_TITLE)
    assert marks.GREETING in text


def test_the_baton_is_charged_to_the_machine_budget(tmp_checkpoint_dir,
                                                    monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "1500")
    store.write_checkpoint("S-1", {
        **_cp(), "working_context": {"recent_decisions": [
            {"text": f"decision {i} " + "padding words " * 20,
             "trust": "inferred"} for i in range(30)]}},
        project_dir=PROJECT, writer=Writer.HUMAN)
    api.write_handoff(PROJECT, "x" * 800)
    assert len(api.render_briefing(PROJECT).text.encode()) <= 1500


# ---- the tenant-scoped host path --------------------------------------------

def test_a_tenant_scoped_host_brief_shows_the_baton_written_for_the_project(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    _seed()
    api.write_handoff(PROJECT, BATON)
    for host in (_api, _mcp, _hermes):
        out = host(capsys, monkeypatch)
        assert AGENT_TITLE in out and f"→ {BATON}" in out


# ---- the format-3 contract ---------------------------------------------------

def test_the_format_version_is_three():
    assert marks.FORMAT_VERSION == 3 == api.BRIEFING_FORMAT


@pytest.mark.parametrize("make,tier", [(lambda: api.write_handoff(PROJECT, BATON),
                                        "agent"), (_human_baton, "human")])
def test_parse_briefing_returns_the_baton_as_a_field(
        tmp_checkpoint_dir, make, tier):
    _seed()
    make()
    parsed = api.parse_briefing(api.render_briefing(PROJECT).text)
    assert parsed.handoff is not None
    assert (parsed.handoff.text, parsed.handoff.tier) == (BATON, tier)
    assert parsed.handoff.ts
    assert parsed.header == marks.GREETING


def test_a_multi_line_baton_round_trips_and_forges_nothing(tmp_checkpoint_dir):
    _seed()
    api.write_handoff(PROJECT, "first\n§ a ruling\n⚠ a warning\n- an item")
    parsed = api.parse_briefing(api.render_briefing(PROJECT).text)
    assert parsed.handoff.text == "first\n§ a ruling\n⚠ a warning\n- an item"
    assert not any("a ruling" in r for r in parsed.standing_rulings)
    assert not any("an item" in i for i in parsed.items)
    assert not any("a warning" in w for w in parsed.warnings)


def test_a_briefing_without_a_baton_parses_with_none(tmp_checkpoint_dir):
    _seed()
    assert api.parse_briefing(api.render_briefing(PROJECT).text).handoff is None


def test_a_briefing_inside_a_transcript_is_stripped_whole_baton_included(
        tmp_checkpoint_dir):
    _seed()
    api.write_handoff(PROJECT, BATON)
    text = api.render_briefing(PROJECT).text
    assert serializer.strip_injected("hello " + text) == "hello "
    assert serializer.strip_injected(text) == ""


# ---- the census: `prepare` is the only brief reader of the baton ------------

# Verbs that legitimately read the baton for themselves: `status` reports that
# one is waiting, `handoff` says what it is superseding.
ACTIVE_HANDOFF_READERS = {"briefing.py", "cli/status.py", "cli/handoff.py"}


def _active_handoff_calls():
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else \
                    getattr(func, "id", "")
                if name == "active_handoff":
                    yield str(path.relative_to(PACKAGE)), node.lineno


def test_only_prepare_and_the_status_and_handoff_verbs_read_the_baton():
    found = {rel for rel, _ in _active_handoff_calls()}
    assert found == ACTIVE_HANDOFF_READERS


def test_prepare_carries_the_baton_and_the_hosts_only_render_it(
        tmp_checkpoint_dir):
    _seed()
    api.write_handoff(PROJECT, BATON)
    got = briefing.prepare(PROJECT, 1_800_000_000.0)
    assert got.handoff["note"] == BATON and got.handoff["tier"] == "agent"
    teammate = briefing.prepare(PROJECT, 1_800_000_000.0, stamps=False)
    assert teammate.handoff is None


def test_a_text_that_ends_inside_the_baton_still_parses_it(tmp_checkpoint_dir):
    _seed(checkpoint=False)
    api.write_handoff(PROJECT, BATON)
    hermes_only = briefing.handoff_block(store.active_handoff(PROJECT))
    parsed = api.parse_briefing(hermes_only.rstrip("\n"))
    assert parsed.handoff is not None and parsed.handoff.text == BATON
    assert parsed.header == ""


def test_an_unbounded_budget_leaves_the_body_unbounded(tmp_checkpoint_dir,
                                                       monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    _seed()
    api.write_handoff(PROJECT, BATON)
    assert briefing._room_after(briefing.handoff_block(
        store.active_handoff(PROJECT))) is None
    assert AGENT_TITLE in api.render_briefing(PROJECT).text
