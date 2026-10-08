"""The notes a recall carries reach every recall host through one presenter
(#1132 PR 9a, D9.5): `daimon recall`, the MCP `daimon_recall` tool and the
viewer's /api/recall show them; recall-inject and action-recall print none."""

import ast
import json
from pathlib import Path

import pytest

import daimon_briefing
from daimon_briefing import cli, config, mcp_tools, recall, store, trust
from tests import _sentinel_drive as drive

HOT = "walrusharbor"
VISIBLE = "a plain decision about the walrusharbor deployment"
QUARANTINED = "the quokkasentinel claim was fabricated by the model"
PROJECT = "/repo/notes"


@pytest.fixture
def stale(tmp_checkpoint_dir, monkeypatch):
    """An indexed project whose next refresh fails: the stale note rides."""
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    recall.rebuild()

    def boom():
        raise OSError("disk")

    monkeypatch.setattr(recall, "_ensure_fresh", boom)


def test_cli_recall_shows_the_note_after_the_rows(stale, capsys):
    assert cli.main(["recall", HOT]) == 0
    out = capsys.readouterr().out.splitlines()
    assert VISIBLE in out[0]
    assert out[-1].startswith("⚠ recall:") and "out of date" in out[-1]


def test_cli_recall_json_keeps_stdout_a_list_and_notes_on_stderr(stale,
                                                                 capsys):
    assert cli.main(["recall", HOT, "--json"]) == 0
    captured = capsys.readouterr()
    assert [r["text"] for r in json.loads(captured.out)] == [VISIBLE]
    assert "out of date" in captured.err


def test_cli_recall_with_no_matches_still_shows_the_note(stale, capsys):
    assert cli.main(["recall", "zzzqqq"]) == 0
    out = capsys.readouterr().out
    assert "no matches" in out and "out of date" in out


def test_a_clean_recall_prints_no_note(tmp_checkpoint_dir, monkeypatch,
                                       capsys):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["recall", HOT]) == 0
    out = capsys.readouterr()
    assert "recall:" not in out.out and out.err == ""


def test_mcp_recall_leads_with_the_note_only_when_there_is_one(stale):
    text = mcp_tools.HANDLERS["daimon_recall"]({"query": HOT})
    first, _, rest = text.partition("\n")
    assert first.startswith("⚠ recall:")
    assert [r["text"] for r in json.loads(rest)] == [VISIBLE]


def test_mcp_recall_clean_result_is_the_bare_list(tmp_checkpoint_dir,
                                                  monkeypatch):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    text = mcp_tools.HANDLERS["daimon_recall"]({"query": HOT})
    assert [r["text"] for r in json.loads(text)] == [VISIBLE]


def test_viewer_recall_carries_the_note_field(stale):
    slug = store.project_slug(PROJECT)
    res = drive.run_http(f"/api/recall?q={HOT}&project={slug}",
                        config.checkpoint_dir(), slug)
    body = json.loads(res.chunks[0])
    assert body["ok"] is True
    assert [r["text"] for r in body["rows"]] == [VISIBLE]
    assert "out of date" in body["note"]


def test_a_closed_bucket_note_names_no_project_and_no_count(
        tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    ledger = config.checkpoint_dir() / store.project_slug(PROJECT) / "trust.jsonl"
    ledger.write_text("garbage line\n")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    text = mcp_tools.HANDLERS["daimon_recall"]({"query": HOT})
    first = text.splitlines()[0]
    assert "trust ledger" in first
    assert store.project_slug(PROJECT) not in text
    assert not any(ch.isdigit() for ch in first)
    assert json.loads(text.split("\n", 1)[1]) == []


@pytest.mark.parametrize("rel", ["cli/inject.py", "cli/action_recall.py"])
def test_recall_inject_and_action_recall_print_no_note(rel):
    src = (Path(daimon_briefing.__file__).parent / rel).read_text(
        encoding="utf-8")
    assert "recall_note" not in src
    names = {n.attr for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name) and n.value.id == "recall"}
    assert "suggest" in names and "query" not in names


def test_and_to_or_fallback_survives_a_fully_withheld_and_result(
        tmp_checkpoint_dir, monkeypatch):
    """The AND query matches only a withheld row; the OR retry must still run
    so the visible partial match is found."""
    both = "walrusharbor quokkasentinel claim fabricated by the model"
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": both, "trust": "inferred"},
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(recall, "_rebuild_forced", lambda path, notes: False)
    trust.propose(text=both, kind="decision", reason="fabricated finding",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    got = recall.query("walrusharbor quokkasentinel", project_dir=PROJECT)
    assert [r["text"] for r in got.rows] == [VISIBLE]


def test_a_failed_forced_rebuild_serves_the_judged_rows_with_a_stale_note(
        tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": QUARANTINED, "trust": "inferred"},
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(recall, "rebuild", boom)
    recall._forced_at.clear()
    trust.propose(text=QUARANTINED, kind="decision", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    got = recall.query("quokkasentinel walrusharbor", project_dir=PROJECT)
    assert [r["text"] for r in got.rows] == [VISIBLE]
    assert "stale" in got.notes


def test_a_closed_item_finds_absent_not_withheld(tmp_checkpoint_dir):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    got = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    item_id = got["working_context"]["recent_decisions"][0]["id"]
    (config.checkpoint_dir() / slug / "trust.jsonl").write_text("garbage\n")
    assert isinstance(recall.find(item_id, slug=slug), recall.Absent)
    assert isinstance(recall.find(item_id, project_dir=None), recall.Absent)
