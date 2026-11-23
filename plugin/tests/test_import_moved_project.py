"""#1384 slice 6: a transcript recorded on the OLD machine imports into the
rebound bucket.

Claude Code writes the working directory it ran in into every row. After the
project moves (new username, new machine, new folder) and the user runs
`daimon bucket remap`, those rows still name the old folder, which the
resolver cannot map to the project (the folder does not exist here), so
`import` used to refuse the one run that fills a fresh machine with history.
The bucket's own identity record keeps every root it has had; a transcript
whose recorded directory is one of those, or lies under one, is the
project's. A transcript from any other folder is still refused.
"""

import pytest

from daimon_briefing import bucket_tools, cli, importer, provenance, store
from tests.test_import_cmd import _STAMPS, _transcript


@pytest.fixture
def moved(tmp_path, tmp_checkpoint_dir, monkeypatch):
    """A bucket recorded under /alice's home, rebound to /bob's by `remap`."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    old_home = str(tmp_path / "alice")  # never created: the old machine
    new_home = tmp_path / "bob"
    project = new_home / "proj"
    project.mkdir(parents=True)
    bucket = tmp_checkpoint_dir / store.location_slug(old_home + "/proj")
    bucket.mkdir(parents=True)
    (bucket / "root").write_text(f"{old_home}/proj\n", encoding="utf-8")
    assert cli.main(["bucket", "remap", f"--from={old_home}",
                     f"--to={new_home}", "--apply"]) == 0
    return old_home, str(project.resolve()), bucket


def _plan(project, paths, tmp_path):
    return importer.plan_import(project, paths, now=2_000_000_000, log_text="",
                                claude_projects=tmp_path / "claude")


def test_remap_keeps_the_old_root_for_the_importer(moved):
    old_home, project, bucket = moved
    assert bucket_tools.former_roots(store.bucket_of(project)) == (
        f"{old_home}/proj",)


def test_a_transcript_recorded_under_the_old_path_imports_after_remap(
        moved, tmp_path):
    old_home, project, _ = moved
    at_root = _transcript(tmp_path / "t", "S-root", _STAMPS[0],
                          cwd=f"{old_home}/proj")
    in_subdir = _transcript(tmp_path / "t", "S-sub", _STAMPS[1],
                            cwd=f"{old_home}/proj/plugin")
    plan = _plan(project, [at_root, in_subdir], tmp_path)
    print(plan.skipped)
    assert [s.session_id for s in plan.sessions] == ["S-root", "S-sub"]


def test_a_transcript_from_an_unrelated_path_is_still_refused(moved, tmp_path):
    old_home, project, _ = moved
    for cwd in (f"{old_home}/other", f"{old_home}/proj-two",
                f"{old_home}/pro", "/somebody/else/proj"):
        stranger = _transcript(tmp_path / "t", "S-x", _STAMPS[0], cwd=cwd)
        with pytest.raises(importer.ProjectMismatch):
            _plan(project, [stranger], tmp_path)


def test_a_project_that_never_moved_accepts_nothing_extra(
        tmp_path, tmp_checkpoint_dir):
    project = tmp_path / "plain"
    project.mkdir()
    stranger = _transcript(tmp_path / "t", "S-x", _STAMPS[0],
                           cwd=str(tmp_path / "elsewhere"))
    assert bucket_tools.former_roots(store.bucket_of(str(project))) == ()
    with pytest.raises(importer.ProjectMismatch):
        _plan(str(project), [stranger], tmp_path)


def test_discovery_finds_the_old_machines_transcripts_after_remap(
        moved, tmp_path):
    """`daimon import` with no `--transcript` walks the host roots and keeps
    the project's files: the old path counts there too."""
    old_home, project, _ = moved
    root = tmp_path / "claude"
    mine = _transcript(root / "-old-slug", "S-mine", _STAMPS[0],
                       cwd=f"{old_home}/proj")
    _transcript(root / "-old-slug", "S-other", _STAMPS[1],
                cwd=f"{old_home}/other")
    found = importer.discover(
        project, provenance.SourceResolver(claude_projects=root))
    assert found.paths == [mine]
