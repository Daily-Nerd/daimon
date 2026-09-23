"""`config.layer_scopes` (#1092): name the directories above a project that
are eligible ruling layers.

Fixtures are built through the shipping writers (`store.record_bucket_root`,
`refutations.assert_ruling`) wherever the writer can actually produce the
state under test. Two places genuinely cannot: `store.record_bucket_root`
resolves its argument through `config.resolve_project_dir` before slugging,
so pointing it at a directory that sits INSIDE a git repo always records the
repo's own root, never that subdirectory's own slug. To prove the git check
in `layer_scopes` excludes those directories for the GIT reason and not
because their bucket lacks a matching record, `_stamp_bucket_root` below
writes the bucket + root marker directly by slug, bypassing that collapse:
this is a marker file, not a ledger row, so it carries no hand-authored
JSONL content. `_legacy_bucket` similarly writes real ledger content through
`refutations.assert_ruling` / `store.append_event`, then deletes the root
marker those calls stamp automatically (#1092 wired every bucket-creating
write to record one) to simulate a bucket that predates this feature.
"""

import subprocess
from pathlib import Path

from daimon_briefing import config, refutations, store


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _stamp_bucket_root(directory: Path, *, names=None) -> None:
    """Give `directory`'s bucket a root record. `names` overrides what the
    record claims to be (default: `directory` itself, i.e. a MATCHING
    record)."""
    slug = store.project_slug(str(directory))
    bucket_dir = config.checkpoint_dir() / slug
    bucket_dir.mkdir(parents=True, exist_ok=True)
    claim = str(directory) if names is None else str(names)
    (bucket_dir / "root").write_text(f"{claim}\n", encoding="utf-8")


def _legacy_bucket_with_active_ruling(directory: Path) -> None:
    refutations.assert_ruling(
        subject="legacy layer subject", verdict="legacy layer verdict",
        scope="legacy layer scope", evidence=["measurement:legacy"],
        channel="cli-tty", ratified=True, project_dir=str(directory))
    slug = store.project_slug(str(directory))
    (config.checkpoint_dir() / slug / "root").unlink()


def _legacy_bucket_without_ruling(directory: Path) -> None:
    store.append_event("item-1", "resolved", project_dir=str(directory))
    slug = store.project_slug(str(directory))
    (config.checkpoint_dir() / slug / "root").unlink()


def _setup_home_work_repo(tmp_path, monkeypatch):
    """`tmp_home/work/repo`, `repo` git-inited, HOME=tmp_home. Returns
    (tmp_home, work, repo)."""
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_home))
    return tmp_home, work, repo


def test_layer_scopes_returns_ancestors_nearest_first(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(repo)) == [str(work), str(tmp_home)]


def test_layer_scopes_worktree_skips_git_dirs_but_keeps_layers(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    # `git worktree add` needs a born HEAD.
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "init"],
                   cwd=str(repo), check=True)
    worktrees_dir = repo / ".claude" / "worktrees"
    worktrees_dir.mkdir(parents=True)
    wt = worktrees_dir / "x"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt)],
                   cwd=str(repo), check=True)

    # Matching root records on every git-shadowed ancestor, to prove they are
    # excluded for the GIT reason, not because their bucket looks unclaimed.
    _stamp_bucket_root(worktrees_dir)
    _stamp_bucket_root(repo / ".claude")
    _stamp_bucket_root(repo)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(wt)) == [str(work), str(tmp_home)]


def test_layer_scopes_bucket_root_mismatch_is_skipped(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    # work's bucket claims to belong to some OTHER directory.
    _stamp_bucket_root(work, names=tmp_home / "someone-else")
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(repo)) == [str(tmp_home)]


def test_layer_scopes_legacy_bucket_with_active_ruling_is_skipped(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    _legacy_bucket_with_active_ruling(work)
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(repo)) == [str(tmp_home)]


def test_layer_scopes_legacy_bucket_without_ruling_is_accepted(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    _legacy_bucket_without_ruling(work)
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(repo)) == [str(work), str(tmp_home)]


def test_layer_scopes_no_bucket_at_all_is_skipped(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    # `work` never wrote anything: no bucket directory at all.
    store.record_bucket_root(str(tmp_home))

    assert config.layer_scopes(str(repo)) == [str(tmp_home)]


def test_layer_scopes_slug_input_returns_empty(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))
    slug = store.project_slug(str(repo))

    assert config.layer_scopes(slug) == []


def test_layer_scopes_relative_path_returns_empty(tmp_path, monkeypatch):
    _setup_home_work_repo(tmp_path, monkeypatch)
    assert config.layer_scopes("relative/path") == []


def test_layer_scopes_falsy_input_returns_empty(tmp_path, monkeypatch):
    _setup_home_work_repo(tmp_path, monkeypatch)
    assert config.layer_scopes(None) == []
    assert config.layer_scopes("") == []


def test_layer_scopes_nonexistent_path_returns_empty(tmp_path, monkeypatch):
    tmp_home, _work, _repo = _setup_home_work_repo(tmp_path, monkeypatch)
    assert config.layer_scopes(str(tmp_home / "never-created")) == []


def test_layer_scopes_project_outside_home_returns_empty(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    outside = tmp_path / "elsewhere" / "proj"
    outside.mkdir(parents=True)

    assert config.layer_scopes(str(outside)) == []


def test_layer_scopes_tenant_scoped_returns_empty(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    assert config.layer_scopes(str(repo)) == []


def test_layer_scopes_home_raises_returns_empty(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))

    def _boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(config.Path, "home", staticmethod(_boom))
    assert config.layer_scopes(str(repo)) == []


def test_layer_scopes_home_is_filesystem_root_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/")
    proj = tmp_path / "proj"
    proj.mkdir()
    # A bucket for "/" itself must never even be consulted. DAIMON_CHECKPOINT_DIR
    # is redirected under tmp_path by the autouse fixture, so this plants the
    # "-" bucket in the test's own isolated store, never the real filesystem root.
    _stamp_bucket_root(Path("/"))

    assert config.layer_scopes(str(proj)) == []


def test_git_shadowed_treats_an_unreadable_dot_git_probe_as_shadowed(
        tmp_path, monkeypatch):
    """An ancestor whose `.git` cannot even be stat'd (permissions, a
    vanished mount) is excluded the same as a real git repo: unreadable is
    not a layer either way."""
    target = tmp_path / "unreadable"
    target.mkdir()

    real_exists = Path.exists

    def _boom(self):
        if self == target / ".git":
            raise OSError("permission denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _boom, raising=False)
    assert config._git_shadowed(target) is True


def test_layer_scopes_symlinked_project_matches_real_path(tmp_path, monkeypatch):
    tmp_home, work, repo = _setup_home_work_repo(tmp_path, monkeypatch)
    store.record_bucket_root(str(work))
    store.record_bucket_root(str(tmp_home))
    link = tmp_home / "repo-link"
    link.symlink_to(repo)

    assert config.layer_scopes(str(link)) == config.layer_scopes(str(repo))
