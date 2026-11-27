"""Who decides what a briefing may do, and where (#1402 round 3).

Three choices used to be made deep inside the shared composer, or by a
default, instead of by the entry point that knows the read:

- whether the checkpoint goes to an LLM provider (the machine paths never
  send it; the CLI and the Hermes hook offer it as an opt-in);
- whether the panels read, and stamp, this project's ledgers (only a read of
  the caller's own bucket, decided once by `ModelRead.own`, and closed when no
  decision was made);
- whether a count or hint may name other buckets (`ModelRead.cross`).
"""
import inspect

import pytest

from daimon_briefing import api, briefing, cli, hooks, mcp_tools, requests
from daimon_briefing import pending
from tests import _furniture_seed as seed
from tests._prepared import annotated_of


def _env(monkeypatch, project=seed.PROJECT):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)


def _stamped(project=seed.PROJECT):
    return [r for r in requests.events(project_dir=project)
            if r.get("event") in ("surfaced", "verdict_surfaced")]


# ---- 1: the entry chooses the LLM render ----------------------------------

@pytest.fixture
def llm_calls(monkeypatch):
    calls = []
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    monkeypatch.setattr(briefing, "_render_llm",
                        lambda cp: calls.append(cp))
    return calls


MACHINE = {
    "api.render_briefing": lambda: api.render_briefing(seed.PROJECT),
    "api.preview_briefing": lambda: api.preview_briefing(seed.PROJECT),
    "mcp.daimon_brief": lambda: mcp_tools.HANDLERS["daimon_brief"](
        {"project": seed.PROJECT}),
}


@pytest.mark.parametrize("name", sorted(MACHINE))
def test_a_machine_entry_never_sends_the_checkpoint_to_an_llm(
        tmp_checkpoint_dir, monkeypatch, llm_calls, name):
    seed.seed()
    _env(monkeypatch)
    MACHINE[name]()
    assert llm_calls == []


def test_machine_brief_never_sends_the_checkpoint_to_an_llm(
        tmp_checkpoint_dir, monkeypatch, llm_calls):
    from daimon_briefing import view
    seed.seed()
    _env(monkeypatch)
    got = briefing.machine_brief(seed.PROJECT, 1.0, loops_pointer=False,
                                 read=view.model_read())
    assert got.state == "briefing" and llm_calls == []


def test_the_hermes_hook_and_the_cli_keep_the_llm_opt_in(
        tmp_checkpoint_dir, monkeypatch, capsys, llm_calls):
    seed.seed()
    _env(monkeypatch)
    hooks.pre_llm_call(session_id="S", user_message="hi",
                       conversation_history=[], is_first_turn=True,
                       model="m", platform="cli")
    assert len(llm_calls) == 1
    assert cli.main(["brief"]) == 0
    assert len(llm_calls) == 2


# ---- 2: no entry can grant the panels by forgetting -----------------------

def test_the_host_api_reading_another_projects_path_shows_and_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch):
    seed.seed()
    _env(monkeypatch, seed.OTHER)
    got = api.render_briefing(seed.PROJECT)
    assert seed.KEPT in got.text
    assert briefing._REQUEST_PANEL_HEADER not in got.text
    assert "decision waiting on you" not in got.text
    assert got.printed == briefing.Printed()
    assert _stamped() == []


def test_the_host_api_reading_its_own_project_still_shows_and_stamps(
        tmp_checkpoint_dir, monkeypatch):
    seed.seed()
    _env(monkeypatch)
    assert briefing._REQUEST_PANEL_HEADER in api.render_briefing(
        seed.PROJECT).text
    assert len(_stamped()) == 2


def test_a_read_with_no_decision_fails_closed(tmp_checkpoint_dir):
    seed.seed()
    got = briefing.prepare(seed.PROJECT, 1.0)
    assert got.furniture.project is None and got.furniture.panels == ()
    assert got.furniture.decision_count == ()
    assert inspect.signature(briefing.machine_brief).parameters[
        "read"].default is inspect.Parameter.empty


# ---- 3: other buckets are named only with the read's grant -----------------

def _count_text(text):
    return [ln for ln in text.splitlines() if "waiting on you here" in ln]


@pytest.fixture
def foreign_counts(monkeypatch):
    calls = []
    real = pending.foreign_counts_typed
    monkeypatch.setattr(pending, "foreign_counts_typed",
                        lambda **k: calls.append(1) or real(**k))
    return calls


@pytest.mark.parametrize("entry", ["mcp", "hermes", "api"])
def test_without_the_grant_a_model_surface_counts_no_other_bucket(
        tmp_checkpoint_dir, monkeypatch, foreign_counts, entry):
    seed.seed()
    _env(monkeypatch)
    if entry == "mcp":
        text = mcp_tools.HANDLERS["daimon_brief"](
            {"project": seed.PROJECT}).text
    elif entry == "hermes":
        text = hooks.pre_llm_call(
            session_id="S", user_message="hi", conversation_history=[],
            is_first_turn=True, model="m", platform="cli")["context"]
    else:
        text = api.render_briefing(seed.PROJECT).text
    assert _count_text(text) == [
        "1 decision waiting on you here - daimon decide"]
    assert foreign_counts == []


@pytest.mark.parametrize("entry", ["mcp", "hermes"])
def test_with_the_grant_a_model_surface_counts_the_other_buckets(
        tmp_checkpoint_dir, monkeypatch, foreign_counts, entry):
    seed.seed()
    _env(monkeypatch)
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    if entry == "mcp":
        text = mcp_tools.HANDLERS["daimon_brief"](
            {"project": seed.PROJECT}).text
    else:
        text = hooks.pre_llm_call(
            session_id="S", user_message="hi", conversation_history=[],
            is_first_turn=True, model="m", platform="cli")["context"]
    assert _count_text(text) == [
        "1 decision waiting on you here (1 elsewhere) - daimon decide"]


def test_the_cli_counts_other_buckets_off_tenant_scope(
        tmp_checkpoint_dir, monkeypatch, capsys):
    seed.seed()
    _env(monkeypatch)
    assert cli.main(["brief"]) == 0
    assert "(1 elsewhere)" in capsys.readouterr().out


def test_the_no_checkpoint_hint_names_other_buckets_only_with_the_grant(
        tmp_checkpoint_dir, monkeypatch):
    seed.seed(checkpoint_for_project=False)
    _env(monkeypatch)
    plain = mcp_tools.HANDLERS["daimon_brief"]({"project": seed.PROJECT}).text
    assert "project(s)" not in plain
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    granted = mcp_tools.HANDLERS["daimon_brief"](
        {"project": seed.PROJECT}).text
    assert "project(s)" in granted


# ---- the notes of a briefing with no body ----------------------------------

def test_a_briefing_with_no_body_prints_the_readers_notes_not_the_body_notes(
        tmp_checkpoint_dir):
    """The imported-history note describes a body that is shown; with none,
    only the reader's notes print (as `machine_brief` and the Hermes
    no-checkpoint path did)."""
    annotated = annotated_of(None, notes=("⚠ a reader note",))
    annotated = annotated._replace(import_note="⚠ Imported history: x")
    text = briefing.compose(annotated).text
    assert "a reader note" in text and "Imported history" not in text
