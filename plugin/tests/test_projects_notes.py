"""The projects listing says when a bucket is closed (#1132 PR 10a, D10.1).

CLI `projects` prints the note after the table (`--json` keeps stdout a JSON
array and sends the note to stderr), MCP `daimon_projects` keeps its array in
`text` and carries the note in `notes`, the viewer's `/api/projects` adds
`notes` beside `projects` and `current`.
"""

import json

import pytest

from daimon_briefing import cli, config, mcp_tools, store
from daimon_briefing.surfaces import Writer

OWN = "/p/proj-notes-own"
CLOSED = "/p/proj-notes-closed"
NOTE = "⚠ 1 project(s) have a trust ledger that cannot be read"


def _write(project, sid="S1"):
    store.write_checkpoint(sid, {
        "session_id": sid, "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "a decision worth keeping", "trust": "inferred"}],
            "open_questions": []},
        "epistemic_snapshot": {}}, project_dir=project, writer=Writer.HUMAN)


@pytest.fixture
def closed_store(tmp_checkpoint_dir):
    _write(OWN)
    _write(CLOSED, "S2")
    bucket = config.checkpoint_dir() / store.project_slug(CLOSED)
    with open(bucket / "trust.jsonl", "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    return tmp_checkpoint_dir


def test_the_cli_prints_the_note_after_the_table(closed_store, capsys):
    assert cli.main(["projects", "--project", OWN]) == 0
    out = capsys.readouterr()
    assert out.out.rstrip().splitlines()[-1] == NOTE


def test_the_cli_json_keeps_stdout_pure_and_notes_go_to_stderr(
        closed_store, capsys):
    assert cli.main(["projects", "--project", OWN, "--json"]) == 0
    out = capsys.readouterr()
    rows = json.loads(out.out)
    assert {r["slug"] for r in rows} == {store.project_slug(OWN),
                                         store.project_slug(CLOSED)}
    assert out.err.strip() == NOTE


def test_a_healthy_listing_has_no_note(tmp_checkpoint_dir, capsys):
    _write(OWN)
    assert cli.main(["projects", "--project", OWN, "--json"]) == 0
    assert capsys.readouterr().err == ""
    assert cli.projects_listing(OWN)[1] == ()


def test_projects_rows_keeps_its_shape(closed_store):
    assert cli.projects_rows(OWN) == cli.projects_listing(OWN)[0]


def test_mcp_keeps_the_array_in_text_and_carries_the_note(closed_store,
                                                          monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", OWN)
    got = mcp_tools.HANDLERS["daimon_projects"]({})
    assert isinstance(json.loads(got.text), list)
    assert got.notes == (NOTE,)


def test_the_viewer_adds_notes_beside_projects_and_current(closed_store):
    from daimon_ui import server
    handler = type("H", (), {})()
    handler.default_slug = store.project_slug(OWN)
    sent = []
    handler._json = sent.append
    server._projects(handler, "/api/projects", {})
    (body,) = sent
    assert set(body) == {"projects", "current", "notes"}
    assert body["notes"] == [NOTE]
    assert body["current"] == store.project_slug(OWN)
    assert {p["slug"] for p in body["projects"]} == {
        store.project_slug(OWN), store.project_slug(CLOSED)}


def test_brief_team_trailer_names_an_author_that_is_not_admitted(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/x")
    remote = config.team_dir() / "team-a"
    (remote / ".git").mkdir(parents=True, exist_ok=True)
    adir = remote / "projects" / "core" / "x" / "authors" / "grace"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    _write(OWN)
    assert cli.main(["brief", "--project", OWN, "--team"]) == 0
    assert "a teammate's tombstones cannot be read" in capsys.readouterr().out
