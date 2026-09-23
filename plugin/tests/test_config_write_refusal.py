"""`config.resolve_project_dir_for_write` (#1092): refuse a write whose
target silently escaped to `Path.home()` because home is itself a git
working tree. A `git init ~` dotfiles setup must not turn a plain
`--project ~/work` into a write against the home bucket.

`resolve_project_dir` already collapses a repo subdirectory to its toplevel
(`daimon/plugin` -> `daimon`, #948) and that MUST keep working for writes
exactly as it does for reads: nothing here may regress it. The two cases
are structurally identical to git (a subdirectory with no `.git` of its own,
under a directory that has one), so the only sound way to tell them apart is
the one CONCRETE symptom: it is `Path.home()` specifically that swallowed
the request. Any other resolved root (e.g. a normal project repo) is left
alone, whether or not it differs from the requested path.
"""
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import config


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_write_refused_when_home_git_repo_swallows_a_subdir(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    _init_git_repo(tmp_home)
    monkeypatch.setenv("HOME", str(tmp_home))
    work = tmp_home / "work"
    work.mkdir()

    with pytest.raises(config.ProjectWriteRefused) as exc_info:
        config.resolve_project_dir_for_write(str(work), allow_slug=False)
    message = str(exc_info.value)
    assert str(work) in message
    assert str(tmp_home) in message


def test_write_allowed_when_repo_subdir_resolves_to_its_own_toplevel(tmp_path, monkeypatch):
    """The #948 contract that MUST keep working: a subdirectory of an
    ordinary project repo (not home) resolves to that repo's root, same as
    a plain read, no refusal."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    repo = tmp_home / "code" / "daimon"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    subdir = repo / "plugin"
    subdir.mkdir()

    result = config.resolve_project_dir_for_write(str(subdir), allow_slug=False)
    assert Path(result).resolve() == repo.resolve()


def test_write_allowed_when_requested_dir_is_not_a_repo_and_home_is_not_git(
        tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    plain = tmp_home / "work"
    plain.mkdir()

    result = config.resolve_project_dir_for_write(str(plain), allow_slug=False)
    assert Path(result).resolve() == plain.resolve()


def test_write_allowed_when_requesting_home_itself(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    _init_git_repo(tmp_home)
    monkeypatch.setenv("HOME", str(tmp_home))

    result = config.resolve_project_dir_for_write(str(tmp_home), allow_slug=False)
    assert Path(result).resolve() == tmp_home.resolve()


def test_write_allowed_when_requested_path_cannot_be_resolved(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    _init_git_repo(tmp_home)
    monkeypatch.setenv("HOME", str(tmp_home))
    work = tmp_home / "work"
    work.mkdir()

    real_resolve = Path.resolve

    def _boom(self, *a, **k):
        if self == Path(str(work)).expanduser():
            raise OSError("stat failed")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", _boom)
    # Falls back to `resolve_project_dir`'s own answer, no guard, no raise.
    result = config.resolve_project_dir_for_write(str(work), allow_slug=False)
    assert result == config.resolve_project_dir(str(work), allow_slug=False)


def test_write_allowed_when_home_cannot_be_determined(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    _init_git_repo(tmp_home)
    monkeypatch.setenv("HOME", str(tmp_home))
    work = tmp_home / "work"
    work.mkdir()

    def _boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(config.Path, "home", staticmethod(_boom))
    result = config.resolve_project_dir_for_write(str(work), allow_slug=False)
    assert Path(result).resolve() == tmp_home.resolve()


def test_write_refusal_never_fires_for_falsy_or_slug_input(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    _init_git_repo(tmp_home)
    monkeypatch.setenv("HOME", str(tmp_home))

    assert config.resolve_project_dir_for_write(None) is None
    assert config.resolve_project_dir_for_write("") == ""
    # A bucket-slug-shaped value never looks like a path, so it passes
    # through untouched regardless of home's git-ness.
    assert config.resolve_project_dir_for_write("-Users-x-proj") == "-Users-x-proj"
