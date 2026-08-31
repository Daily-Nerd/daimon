"""#889: a cross-project recall hit must not render as the reader's own memory.

`search` selects `i.project_slug` per row and the render line never printed it,
so a foreign row and a local row looked identical on every surface except
`--json`. `[author]` reads like the answer and is not: `config.author()` is a
machine identity, one constant across every project on a workstation.

The marker is deliberately absent on same-project rows. A label that never
varies is read as furniture and stops being seen, which would reintroduce the
defect wearing a fix.
"""

from daimon_briefing import cli, recall, store


def _cp(sid, text):
    return {
        "session_id": sid,
        "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": text, "trust": "inferred"}]},
    }


# ---- the shared phrasing ---------------------------------------------------


def test_describe_scope_is_silent_for_the_readers_own_slug():
    assert recall.describe_scope({"project_slug": "-p-mine"}, "-p-mine") is None


def test_describe_scope_names_a_foreign_slug():
    assert recall.describe_scope(
        {"project_slug": "-p-theirs"}, "-p-mine") == "from -p-theirs"


def test_describe_scope_is_silent_when_the_reader_has_no_slug():
    """An unknown project cannot say what is foreign to it, and guessing would
    label every row of an all-projects search."""
    assert recall.describe_scope({"project_slug": "-p-theirs"}, None) is None


def test_describe_scope_is_silent_on_a_row_with_no_slug():
    """Rotated-out pre-stamp checkpoints index NULL-slug on purpose. A row
    that never claimed a project must not be described as foreign."""
    assert recall.describe_scope({"project_slug": None}, "-p-mine") is None
    assert recall.describe_scope({}, "-p-mine") is None


# ---- the recall surface ----------------------------------------------------


def test_cli_recall_all_projects_marks_the_foreign_row(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    """The live defect: before this, both lines rendered identically."""
    proj_a = str((tmp_path / "proj-a").resolve())
    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", _cp("S-a", "lemur work in a"),
                           project_dir=proj_a)
    store.write_checkpoint("S-b", _cp("S-b", "lemur work in b"),
                           project_dir=proj_b)

    assert cli.main(["recall", "lemur", "--project", proj_a,
                     "--all-projects"]) == 0
    out = capsys.readouterr().out
    slug_b = store.project_slug(proj_b)
    assert f"from {slug_b}" in out, "the foreign row is unmarked"
    slug_a = store.project_slug(proj_a)
    assert f"from {slug_a}" not in out, "the reader's own row was marked"


def test_cli_recall_scoped_search_marks_nothing(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    """The common case pays no noise: every row is the reader's own."""
    proj = str((tmp_path / "proj-only").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", _cp("S-a", "lemur work here"),
                           project_dir=proj)
    assert cli.main(["recall", "lemur", "--project", proj]) == 0
    out = capsys.readouterr().out
    assert "lemur work here" in out
    assert "from " not in out


def test_cli_recall_json_is_unchanged(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    """--json already carried project_slug; the marker is a human-render
    concern and must not become a second encoding of the same fact."""
    proj = str((tmp_path / "proj-json").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", _cp("S-a", "lemur json"), project_dir=proj)
    assert cli.main(["recall", "lemur", "--project", proj, "--json"]) == 0
    out = capsys.readouterr().out
    assert '"project_slug"' in out
    assert "from " not in out


# ---- the inject surface ----------------------------------------------------


def test_inject_line_marks_a_foreign_row():
    """The two surfaces share one phrasing so they can never disagree about
    where a memory came from.

    `suggest` cannot cross a scope today: it takes no `slug` parameter and
    derives one from `project_dir`. This exercises the line builder directly,
    because the guard has to exist BEFORE the capability that would need it,
    which is the whole timing argument in #889."""
    row = {"kind": "decision", "session_id": "S-x", "text": "a foreign claim",
           "trust": "inferred", "created": 0, "project_slug": "-p-theirs"}
    line = cli._suggest_line(row, ["lemur"], 0.0, own_slug="-p-mine")
    assert "from -p-theirs" in line


def test_inject_line_is_silent_for_the_readers_own_row():
    row = {"kind": "decision", "session_id": "S-x", "text": "my own claim",
           "trust": "inferred", "created": 0, "project_slug": "-p-mine"}
    line = cli._suggest_line(row, ["lemur"], 0.0, own_slug="-p-mine")
    assert "from -p-mine" not in line
