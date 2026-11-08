"""hostgit SEALED posture.

Recorder tests assert on the argv, env, cwd and spawn count hostgit builds;
real-git tests (15, 16) build tmp repos and a config key that points one at
the other. Tests use tmp dirs only.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import config, hostgit, worldcheck
from tests.test_hostgit import Recorder

SEALED = hostgit.Posture.SEALED
AMBIENT = hostgit.Posture.AMBIENT

POISON = {
    "GIT_INDEX_FILE": "/poison/index",
    "GIT_CEILING_DIRECTORIES": "/poison/ceil",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.sshCommand",
    "GIT_CONFIG_VALUE_0": "/poison/ssh",
    "GIT_SSH_COMMAND": "/poison/ssh",
    "GIT_EXEC_PATH": "/poison/exec",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/poison/objects",
    "GIT_DIR": "/poison/gitdir",
    "GIT_WORK_TREE": "/poison/tree",
    "GIT_COMMON_DIR": "/poison/common",
    "GIT_NAMESPACE": "poison",
    "GIT_CONFIG_GLOBAL": "/poison/global",
}


def fake_repo(root: Path, url: str | None = "https://github.com/acme/widget.git",
              head="ref: refs/heads/main\n", branch="main") -> Path:
    """A minimal main gitdir (no git binary involved)."""
    root.mkdir(parents=True, exist_ok=True)
    git = root / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head, encoding="utf-8")
    if branch:
        (git / "refs" / "heads" / branch).write_text("0" * 40 + "\n",
                                                     encoding="utf-8")
    cfg = "[core]\n\trepositoryformatversion = 0\n"
    if url:
        cfg += f'[remote "origin"]\n\turl = {url}\n'
    (git / "config").write_text(cfg, encoding="utf-8")
    return root


@pytest.fixture
def world(tmp_path):
    """tmp ceiling with one fake repo `repo` below it, SEALED configured."""
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    repo = fake_repo(ceiling / "repo")
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    return ceiling, repo


class SealedRecorder(Recorder):
    """A Recorder that keeps the config-gate listing (`config ... --list -z`,
    one per checkout, run by `project()`) apart in `listings`, answering it
    with an empty, allowed config, so `calls` holds only what a test asks for.
    The gate itself is covered by test_hostgit_config_gate.py."""

    def __init__(self):
        super().__init__()
        self.listings: list[dict] = []

    def __call__(self, argv, env, cwd, timeout, stdin=None):
        if "--list" in argv and "-z" in argv:
            self.listings.append({"argv": list(argv), "env": env, "cwd": cwd,
                                  "timeout": timeout})
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return super().__call__(argv, env, cwd, timeout, stdin)


@pytest.fixture
def rec(monkeypatch):
    recorder = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", recorder)
    return recorder


# ---- posture --------------------------------------------------------------


def test_sealed_is_available_and_configure_takes_a_ceiling(tmp_path):
    hostgit.configure(posture="sealed", ceiling=tmp_path)
    assert hostgit.posture() is SEALED
    assert hostgit.ceiling() == str(tmp_path.resolve())


def test_the_default_ceiling_is_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    hostgit.configure(posture=SEALED)
    assert hostgit.ceiling() == str(tmp_path.resolve())


# ---- project(): the protocol ----------------------------------------------


def test_project_walks_up_in_python_and_spawns_only_the_config_listing(
        world, rec):
    ceiling, repo = world
    sub = repo / "plugin" / "deep"
    sub.mkdir(parents=True)
    got = hostgit.project(sub)
    assert got == hostgit.Checkout(root=str(repo.resolve()),
                                   gitdir=str(repo.resolve() / ".git"))
    assert rec.calls == []
    assert len(rec.listings) == 1


def _refusal_cases(tmp_path):
    """(code, path-builder) pairs; each builds a project dir below a ceiling."""
    def gitfile(ceiling):
        victim = fake_repo(ceiling / "victim")
        p = ceiling / "p"
        p.mkdir()
        (p / ".git").write_text(f"gitdir: {victim}/.git\n", encoding="utf-8")
        return p

    def symlinked(ceiling):
        victim = fake_repo(ceiling / "victim")
        p = ceiling / "p"
        p.mkdir()
        (p / ".git").symlink_to(victim / ".git", target_is_directory=True)
        return p

    def empty(ceiling):
        fake_repo(ceiling / "outer")
        p = ceiling / "outer" / "p"
        (p / ".git").mkdir(parents=True)
        return p

    def commondir(ceiling):
        p = fake_repo(ceiling / "p")
        (p / ".git" / "commondir").write_text("../victim/.git\n",
                                              encoding="utf-8")
        return p

    def missing(part):
        def build(ceiling):
            p = fake_repo(ceiling / "p")
            target = p / ".git" / part
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return p
        return build

    def config_link(ceiling):
        p = fake_repo(ceiling / "p")
        other = ceiling / "other-config"
        other.write_text("[remote \"origin\"]\n\turl = https://x/y\n",
                         encoding="utf-8")
        (p / ".git" / "config").unlink()
        (p / ".git" / "config").symlink_to(other)
        return p

    def no_git(ceiling):
        p = ceiling / "plain"
        p.mkdir()
        return p

    def ceiling_is_repo(ceiling):
        fake_repo(ceiling)  # the ceiling itself holds the repo (dotfiles)
        p = ceiling / "work"
        p.mkdir()
        return p

    def above_ceiling(ceiling):
        fake_repo(ceiling.parent / "enclosing-above")
        return None  # handled below

    def outside(ceiling):
        outer = ceiling.parent / "elsewhere"
        outer.mkdir()
        return outer

    def link_outside(ceiling):
        outer = ceiling.parent / "elsewhere2"
        fake_repo(outer)
        link = ceiling / "link"
        link.symlink_to(outer, target_is_directory=True)
        return link

    def not_a_dir(ceiling):
        f = ceiling / "file.txt"
        f.write_text("x", encoding="utf-8")
        return f

    return [
        ("gitfile", gitfile), ("symlink", symlinked),
        ("empty-gitdir", empty), ("commondir", commondir),
        ("empty-gitdir", missing("HEAD")), ("empty-gitdir", missing("objects")),
        ("empty-gitdir", missing("refs")), ("symlink", config_link),
        ("no-git-below-ceiling", no_git),
        ("no-git-below-ceiling", ceiling_is_repo),
        ("outside-ceiling", outside), ("outside-ceiling", link_outside),
        ("not-a-dir", not_a_dir),
    ]


REFUSAL_IDS = ["gitfile", "symlink", "empty-dot-git", "commondir", "no-HEAD",
               "no-objects", "no-refs", "config-symlink", "no-git",
               "repo-at-ceiling", "outside", "symlink-outside", "not-a-dir"]


@pytest.mark.parametrize("index", range(len(REFUSAL_IDS)), ids=REFUSAL_IDS)
def test_a_refused_checkout_answers_with_its_code_and_spawns_nothing(
        tmp_path, rec, index):
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    code, build = _refusal_cases(tmp_path)[index]
    path = build(ceiling)
    assert hostgit.project(path) == hostgit.Refused(code)
    assert hostgit.origin_url(path) is None
    assert hostgit.branch(path) is None
    assert hostgit.remote_listing(path) is None
    assert hostgit.refs_dir(path) is None
    assert rec.calls == [] and rec.listings == []
    assert (str(path), code) in hostgit.refusals()


def test_a_parent_repo_above_the_ceiling_is_never_adopted(tmp_path, rec):
    above = fake_repo(tmp_path / "above")
    ceiling = above / "home"
    ceiling.mkdir()
    project = ceiling / "proj"
    project.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    assert hostgit.project(project) == hostgit.Refused("no-git-below-ceiling")
    assert rec.calls == []


def test_a_subdirectory_of_a_repo_below_the_ceiling_resolves_to_that_repo(
        world, rec):
    # The ordinary project subdirectory (#948): the first ancestor holding a
    # .git decides, and only a valid one is accepted.
    _ceiling, repo = world
    sub = repo / "plugin"
    sub.mkdir()
    assert hostgit.project(sub).root == str(repo.resolve())


def test_refusals_are_recorded_once_per_path_and_logged(world, caplog):
    ceiling, _repo = world
    plain = ceiling / "plain"
    plain.mkdir()
    with caplog.at_level("WARNING", logger=hostgit.__name__):
        hostgit.project(plain)
        hostgit.project(plain)
    assert hostgit.refusals() == [(str(plain), "no-git-below-ceiling")]
    assert len([r for r in caplog.records if "no-git-below-ceiling" in r.getMessage()]) == 1


def test_refusals_are_cleared_by_reset(world):
    ceiling, _repo = world
    (ceiling / "p").mkdir()
    hostgit.project(ceiling / "p")
    assert hostgit.refusals()
    hostgit._reset()
    assert hostgit.refusals() == []


# ---- SEALED reads: argv, env, cwd (red tests 1-4, 6) ----------------------


def _spawning_calls(repo):
    return [
        ("origin_url", lambda: hostgit.origin_url(repo)),
        ("remote_listing", lambda: hostgit.remote_listing(repo)),
    ]


@pytest.mark.parametrize("name", ["origin_url", "remote_listing"])
def test_sealed_spawn_env_is_fresh_and_pinned(world, rec, monkeypatch, name):
    _ceiling, repo = world
    rec.answer("https://github.com/acme/widget.git\n")
    for key, value in POISON.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "C")
    dict(_spawning_calls(repo))[name]()
    real = repo.resolve()
    env = rec.calls[0]["env"]
    assert env["GIT_DIR"] == str(real / ".git")
    assert env["GIT_COMMON_DIR"] == str(real / ".git")
    assert env["GIT_WORK_TREE"] == str(real)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["PATH"] == "/usr/bin:/bin" and env["LANG"] == "C"
    pins = {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
            "GIT_OPTIONAL_LOCKS"}
    assert {k for k in env if k.startswith("GIT_")} == pins
    allowed = pins | {"PATH", "LANG", "LC_ALL", "TMPDIR"}
    assert set(env) <= allowed
    for key in POISON:
        if key not in pins:
            assert key not in env


@pytest.mark.parametrize("poison", sorted(POISON))
def test_each_poisoned_variable_stays_out_of_the_child(world, rec, monkeypatch,
                                                       poison):
    _ceiling, repo = world
    rec.answer("u\n")
    monkeypatch.setenv(poison, POISON[poison])
    hostgit.origin_url(repo)
    env = rec.calls[0]["env"]
    if poison in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                  "GIT_CONFIG_GLOBAL"):
        assert env[poison] != POISON[poison]  # replaced by the pin
    else:
        assert poison not in env


def test_sealed_argv_names_no_dash_C_no_rev_parse_and_runs_in_the_root(world, rec):
    _ceiling, repo = world
    rec.answer("u\n")
    hostgit.origin_url(repo)
    call = rec.calls[0]
    argv = call["argv"]
    assert argv[:5] == ["git", "-c", "core.fsmonitor=false", "-c",
                        f"core.hooksPath={hostgit._hooks_dir()}"]
    assert "-C" not in argv
    assert "rev-parse" not in argv and "--show-toplevel" not in argv
    assert call["cwd"] == str(repo.resolve())


def test_sealed_config_reads_use_the_one_file_and_no_includes(world, rec):
    _ceiling, repo = world
    rec.answer("https://github.com/acme/widget.git\n")
    assert hostgit.origin_url(repo) == "https://github.com/acme/widget.git"
    cfg = str(repo.resolve() / ".git" / "config")
    assert rec.calls[0]["argv"][5:] == [
        "config", "--file", cfg, "--no-includes", "--get", "remote.origin.url"]
    rec.answer("remote.origin.url https://github.com/acme/widget.git\n")
    assert "github.com" in hostgit.remote_listing(repo)
    assert rec.calls[1]["argv"][5:] == [
        "config", "--file", cfg, "--no-includes", "--get-regexp",
        r"^remote\..*\.url$"]


def test_remote_listing_keeps_empty_apart_from_failed(world, rec):
    _ceiling, repo = world
    rec.answer("", rc=1)  # git config: no match
    assert hostgit.remote_listing(repo) == ""
    rec.answer("", rc=128)
    assert hostgit.remote_listing(repo) is None


def test_branch_is_read_from_head_without_git(world, rec):
    _ceiling, repo = world
    assert hostgit.branch(repo) == "main"
    git = repo / ".git"
    (git / "HEAD").write_text("0123456789012345678901234567890123456789\n")
    assert hostgit.branch(repo) is None  # detached
    (git / "HEAD").write_text("ref: refs/heads/unborn\n")
    assert hostgit.branch(repo) is None  # unborn, like rev-parse
    (git / "packed-refs").write_text(
        "# pack-refs\n" + "a" * 40 + " refs/heads/unborn\n")
    assert hostgit.branch(repo) == "unborn"  # packed ref counts
    assert rec.calls == []


def test_user_name_spawns_nothing_and_author_falls_to_daimon_author(
        world, rec, monkeypatch):
    assert hostgit.user_name(os.getcwd()) == ""
    assert rec.calls == []
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    config._git_user_name_in.cache_clear()
    assert config.author() == "Ada"
    monkeypatch.delenv("DAIMON_AUTHOR")
    monkeypatch.setattr(config.getpass, "getuser", lambda: "osuser")
    assert config.author() == "osuser"
    assert rec.calls == []


def test_has_entry_is_a_real_directory_only(world):
    ceiling, repo = world
    assert hostgit.has_entry(repo) is True
    link = ceiling / "lnk"
    link.mkdir()
    (link / ".git").symlink_to(repo / ".git", target_is_directory=True)
    assert hostgit.has_entry(link) is False
    plain = ceiling / "plain"
    plain.mkdir()
    assert hostgit.has_entry(plain) is False


def test_refs_dir_returns_the_validated_gitdir_only(world):
    _ceiling, repo = world
    assert hostgit.refs_dir(repo) == repo.resolve() / ".git"


# ---- github repo + gh -----------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/widget.git", "acme/widget"),
    ("https://github.com/acme/widget", "acme/widget"),
    ("git@github.com:acme/widget.git", "acme/widget"),
    ("ssh://git@github.com/acme/widget.git", "acme/widget"),
    ("https://tok@github.com/acme/widget.git", "acme/widget"),
    ("https://gitlab.com/acme/widget.git", None),
    ("https://github.com.evil.example/acme/widget.git", None),
    ("not a url", None),
])
def test_github_repo_is_derived_from_origin(world, rec, url, expected):
    _ceiling, repo = world
    rec.answer(url + "\n")
    assert hostgit.github_repo(repo) == expected


def test_sealed_gh_runs_in_a_private_cwd_with_an_allowlisted_env(
        monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(hostgit, "_spawn_async",
                        lambda argv, env, cwd, stdin: seen.update(
                            argv=argv, env=env, cwd=cwd) or "p")
    hostgit.configure(posture=SEALED, ceiling=tmp_path)
    for key, value in {**POISON, "GH_TOKEN": "t", "GITHUB_TOKEN": "t2",
                       "GH_REPO": "evil/repo", "GH_HOST": "github.com",
                       "GH_EDITOR": "/bin/evil", "GH_CONFIG_DIR": "/cfg",
                       "HOME": "/home/u", "PATH": "/bin"}.items():
        monkeypatch.setenv(key, value)
    project = tmp_path / "proj"
    project.mkdir()
    hostgit.gh_popen(["/bin/gh", "pr", "view", "7", "--repo", "a/b"], project)
    env = seen["env"]
    assert not [k for k in env if k.startswith("GIT_") and k != "GIT_TERMINAL_PROMPT"]
    assert env["GH_TOKEN"] == "t" and env["GITHUB_TOKEN"] == "t2"
    assert env["GH_HOST"] == "github.com" and env["GH_CONFIG_DIR"] == "/cfg"
    assert env["HOME"] == "/home/u"
    assert "GH_REPO" not in env and "GH_EDITOR" not in env
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert seen["cwd"] != str(project)
    assert Path(seen["cwd"]).is_dir() and list(Path(seen["cwd"]).iterdir()) == []
    assert seen["argv"][-2:] == ["--repo", "a/b"]


def test_sealed_gh_probes_carry_repo_derived_from_the_sealed_origin(
        world, rec, monkeypatch):
    _ceiling, repo = world
    rec.answer("https://github.com/acme/widget.git\n")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: "/bin/gh")
    monkeypatch.setattr(worldcheck, "_remote_state", lambda p: "github")
    plan = {(worldcheck.PR_STATE, "7"): type(
        "C", (), {"kind": "pr", "num": "7"})()}
    why = {}
    probes = worldcheck._gh_probes(plan, str(repo), why)
    (probe,) = probes.values()
    assert probe.argv[:4] == ["/bin/gh", "pr", "view", "7"]
    assert probe.argv[-2:] == ["--repo", "acme/widget"]


def test_sealed_gh_probes_skip_when_origin_is_not_github(world, rec, monkeypatch):
    _ceiling, repo = world
    rec.answer("https://gitlab.com/acme/widget.git\n")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: "/bin/gh")
    monkeypatch.setattr(worldcheck, "_remote_state", lambda p: "github")
    plan = {(worldcheck.PR_STATE, "7"): type(
        "C", (), {"kind": "pr", "num": "7"})()}
    why = {}
    assert worldcheck._gh_probes(plan, str(repo), why) == {}
    assert set(why.values()) == {"no-remote"}


# ---- callers (red test 20 and the write seam) -----------------------------


def test_remote_state_has_a_fourth_value_for_a_refused_checkout(world, rec):
    ceiling, _repo = world
    plain = ceiling / "plain"
    plain.mkdir()
    assert worldcheck._remote_state(str(plain)) == "git-refused"
    assert worldcheck._REMOTE_SKIP_REASON["git-refused"] == "git-refused"
    assert "git-refused" in worldcheck.SKIP_REASONS
    assert rec.calls == []


def test_remote_state_for_a_good_checkout_reads_the_one_file_config(world, rec):
    _ceiling, repo = world
    rec.answer("remote.origin.url https://github.com/acme/widget.git\n")
    assert worldcheck._remote_state(str(repo)) == "github"
    rec.answer("remote.origin.url https://gitlab.com/acme/widget.git\n")
    assert worldcheck._remote_state(str(repo)) == "no-remote"


def test_probe_branch_is_none_not_missing_for_a_refused_checkout(world):
    ceiling, repo = world
    assert worldcheck._probe_branch(str(repo), "main") == "EXISTS"
    assert worldcheck._probe_branch(str(repo), "nope") == "MISSING"
    plain = ceiling / "plain"
    plain.mkdir()
    assert worldcheck._probe_branch(str(plain), "main") is None


def test_reads_keep_raw_on_a_refusal_and_writes_raise(world):
    ceiling, repo = world
    plain = ceiling / "plain"
    plain.mkdir()
    real = str(plain.resolve())
    assert config.resolve_project_root(real) == real  # contract: its own root
    assert config.resolve_project_dir(str(plain)) == real
    with pytest.raises(config.ProjectWriteRefused,
                       match="no-git-below-ceiling"):
        config.resolve_project_dir_for_write(str(plain))
    # a good checkout still writes, and a subdirectory still folds to its root
    sub = repo / "plugin"
    sub.mkdir()
    assert config.resolve_project_dir_for_write(str(sub)) == str(repo.resolve())


def test_a_slug_is_not_a_checkout_and_passes_the_write_seam(world):
    assert config.resolve_project_dir_for_write("-Users-x-proj") == "-Users-x-proj"


# ---- real git: tests 15 and 16 --------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git not installed")


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _real_repo(path: Path, url: str) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Real")
    _git(path, "config", "user.email", "r@x")
    _git(path, "remote", "add", "origin", url)
    (path / "f").write_text("f\n", encoding="utf-8")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "init")
    return path


@needs_git
def test_core_worktree_in_the_repo_config_cannot_move_the_answer(tmp_path):
    home = tmp_path / "home"
    mine = _real_repo(home / "mine", "https://github.com/me/mine.git")
    victim = _real_repo(home / "victim", "https://github.com/them/victim.git")
    _git(mine, "config", "core.worktree", str(victim))

    # AMBIENT is git's own answer, and with this key set it is the other
    # checkout. This is the documented current behavior of the default posture.
    hostgit.configure(posture=AMBIENT)
    config.resolve_project_root.cache_clear()
    assert Path(hostgit.project(mine).root).resolve() == victim.resolve()

    # SEALED does not answer at all: the key is outside the config allowlist,
    # so the checkout is refused before any git runs on it.
    hostgit.configure(posture=SEALED, ceiling=home)
    config.resolve_project_root.cache_clear()
    assert hostgit.project(mine) == hostgit.Refused("config-key:core.worktree")
    assert hostgit.origin_url(mine) is None
    assert hostgit.branch(mine) is None
    # ...and the sibling it pointed at is untouched.
    assert hostgit.origin_url(victim) == "https://github.com/them/victim.git"


@needs_git
def test_includes_and_worktree_config_are_refused_in_sealed_and_followed_in_ambient(
        tmp_path):
    home = tmp_path / "home"
    mine = _real_repo(home / "mine", "https://github.com/me/mine.git")
    extra = tmp_path / "extra.cfg"
    extra.write_text('[remote "origin"]\n\turl = https://github.com/x/included.git\n'
                     "[user]\n\tname = Included\n", encoding="utf-8")
    cfg = mine / ".git" / "config"  # the include goes first so it answers first
    cfg.write_text(f"[include]\n\tpath = {extra}\n"
                   + cfg.read_text(encoding="utf-8"), encoding="utf-8")
    _git(mine, "config", "extensions.worktreeConfig", "true")
    (mine / ".git" / "config.worktree").write_text(
        '[remote "origin"]\n\turl = https://github.com/x/worktree.git\n'
        "[user]\n\tname = WorktreeName\n", encoding="utf-8")

    hostgit.configure(posture=AMBIENT)
    ambient = hostgit.origin_url(mine)
    assert ambient == "https://github.com/x/included.git"  # documented current behavior
    assert hostgit.user_name(mine) in ("Included", "WorktreeName")

    hostgit.configure(posture=SEALED, ceiling=home)
    assert hostgit.project(mine) == hostgit.Refused("worktree-config")
    assert hostgit.origin_url(mine) is None
    assert hostgit.remote_listing(mine) is None
    assert hostgit.user_name(mine) == ""
    (mine / ".git" / "config.worktree").unlink()
    assert hostgit.project(mine) == hostgit.Refused("config-key:include.path")


@needs_git
def test_a_normal_checkout_answers_the_same_in_both_postures(tmp_path):
    home = tmp_path / "home"
    repo = _real_repo(home / "r", "git@github.com:me/r.git")
    sub = repo / "sub"
    sub.mkdir()
    hostgit.configure(posture=AMBIENT)
    ambient = (Path(hostgit.project(sub).root).resolve(), hostgit.branch(repo),
               hostgit.origin_url(repo))
    hostgit.configure(posture=SEALED, ceiling=home)
    sealed = (Path(hostgit.project(sub).root), hostgit.branch(repo),
              hostgit.origin_url(repo))
    assert sealed == ambient
    assert "github.com" in hostgit.remote_listing(repo)
    assert hostgit.github_repo(repo) == "me/r"


# ---- daimon status prints refused paths -----------------------------------


def test_daimon_status_prints_a_line_per_refused_path(tmp_path, monkeypatch,
                                                      capsys):
    from daimon_briefing import cli
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    plain = ceiling / "plain"
    plain.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    cli.main(["status", "--project", str(plain)])
    out = capsys.readouterr().out
    assert f"git: {plain.resolve()} refused (no-git-below-ceiling)" in out


def test_daimon_status_is_unchanged_when_nothing_was_refused(capsys, tmp_path):
    from daimon_briefing import cli
    cli.main(["status", "--project", str(tmp_path)])
    assert "refused (" not in capsys.readouterr().out


# ---- sidecars under SEALED -------------------------------------------------


def test_sealed_sidecar_and_clone_refuse_an_untrusted_directory(
        tmp_path, rec):
    team = tmp_path / "team"
    team.mkdir()
    team.chmod(0o777)
    sidecar_dir = team / "x"
    (sidecar_dir / ".git").mkdir(parents=True)
    sidecar_dir.chmod(0o775)
    hostgit.configure(posture=SEALED, ceiling=tmp_path)
    ran = hostgit.sidecar(sidecar_dir, "status")
    assert ran.returncode == 125 and "writable" in ran.stderr
    cloned = hostgit.clone("https://example.invalid/r.git", team / "y",
                           timeout=5)
    assert cloned.returncode == 125
    assert rec.calls == []
