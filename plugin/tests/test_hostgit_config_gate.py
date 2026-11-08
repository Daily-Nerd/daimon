"""hostgit SEALED posture: the repository config allowlist gate (#1308, slice 1).

Invariant: a sealed project takes nothing executable from the repository's
config. `project()` lists the one-file config before it hands out a Checkout and
refuses on the first key outside CONFIG_ALLOWLIST. Marker tests use real git and
scripts that drop a file when run; a control proves plain git fires them. Tests
use tmp dirs only.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import hostgit
from tests.test_hostgit import Recorder
from tests.test_hostgit_sealed import SEALED, fake_repo

needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git not installed")

IDENT = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
         "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}


class Spy:
    """Wraps the real `_spawn`, recording every call."""

    def __init__(self, real):
        self.real = real
        self.calls: list[dict] = []

    def __call__(self, argv, env, cwd, timeout, stdin=None):
        self.calls.append({"argv": list(argv), "env": env, "cwd": cwd,
                           "timeout": timeout})
        return self.real(argv, env, cwd, timeout, stdin)

    @property
    def listings(self):
        return [c for c in self.calls if "--list" in c["argv"]]


@pytest.fixture
def spy(monkeypatch):
    spy = Spy(hostgit._spawn)
    monkeypatch.setattr(hostgit, "_spawn", spy)
    return spy


def _plain(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(IDENT)
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=60, env=env)


def _marker_script(path: Path, markers: Path, name: str, body="") -> Path:
    path.write_text(f"#!/bin/sh\ntouch '{markers}/{name}'\n{body}\n",
                    encoding="utf-8")
    path.chmod(0o755)
    return path


DRIVERS = ("clean", "smudge", "textconv", "external", "merge")


def _driver_config(tmp_path: Path, markers: Path) -> str:
    s = tmp_path / "scripts"
    s.mkdir(exist_ok=True)
    clean = _marker_script(s / "clean", markers, "clean", "cat")
    smudge = _marker_script(s / "smudge", markers, "smudge", "cat")
    textconv = _marker_script(s / "textconv", markers, "textconv", 'cat "$1"')
    external = _marker_script(s / "external", markers, "external")
    merge = _marker_script(s / "merge", markers, "merge")
    return (f'[filter "m"]\n\tclean = {clean}\n\tsmudge = {smudge}\n'
            f'[diff "t"]\n\ttextconv = {textconv}\n'
            f'[diff]\n\texternal = {external}\n'
            f'[merge "d"]\n\tdriver = {merge} %O %A %B\n')


def _fired(markers: Path) -> list[str]:
    return sorted(p.name for p in markers.iterdir())


def _clear(markers: Path):
    for m in markers.iterdir():
        m.unlink()


@pytest.fixture
def lab(tmp_path):
    """(ceiling, repo, markers, drivers): a real repo below a tmp ceiling with a
    `.gitattributes` binding *.txt to filter/diff/merge drivers, one commit,
    and the driver config text (not yet written anywhere)."""
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()
    repo = ceiling / "repo"
    repo.mkdir()
    assert _plain(repo, "init", "-q", "-b", "main").returncode == 0
    (repo / ".gitattributes").write_text("*.txt filter=m diff=t merge=d\n",
                                         encoding="utf-8")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    assert _plain(repo, "add", ".gitattributes", "a.txt").returncode == 0
    assert _plain(repo, "commit", "-q", "-m", "seed").returncode == 0
    assert _plain(repo, "remote", "add", "origin",
                  "https://github.com/acme/widget.git").returncode == 0
    drivers = _driver_config(tmp_path, markers)
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    return ceiling, repo, markers, drivers


def _arm_via_include(tmp_path, repo, drivers):
    extra = tmp_path / "drivers.cfg"
    extra.write_text(drivers, encoding="utf-8")
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + f"[include]\n\tpath = {extra}\n", encoding="utf-8")


def _arm_directly(repo, drivers):
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8") + drivers, encoding="utf-8")


def _exercise_plain(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    _plain(repo, "add", "a.txt")
    _plain(repo, "diff", "--cached")
    _plain(repo, "show", "HEAD", "--", "a.txt")
    (repo / "a.txt").write_text("changed again\n", encoding="utf-8")
    _plain(repo, "diff")


# ---- marker tests ----------------------------------------------------------


@needs_git
def test_control_plain_git_fires_the_drivers_an_included_config_defines(
        tmp_path, lab):
    _ceiling, repo, markers, drivers = lab
    _arm_via_include(tmp_path, repo, drivers)
    _clear(markers)
    _exercise_plain(repo)
    fired = _fired(markers)
    assert {"clean", "textconv", "external"} <= set(fired), fired


@needs_git
def test_an_included_config_is_refused_before_anything_but_the_listing_runs(
        tmp_path, lab, spy):
    _ceiling, repo, markers, drivers = lab
    _arm_via_include(tmp_path, repo, drivers)
    _clear(markers)
    got = hostgit.project(repo)
    assert got == hostgit.Refused("config-key:include.path")
    assert hostgit.origin_url(repo) is None
    assert hostgit.branch(repo) is None
    assert len(spy.calls) == 1 and spy.calls == spy.listings  # the listing, once
    assert _fired(markers) == []


@needs_git
def test_drivers_written_straight_into_the_config_refuse_on_the_first_key(
        tmp_path, lab, spy):
    _ceiling, repo, markers, drivers = lab
    _arm_directly(repo, drivers)
    _clear(markers)
    assert hostgit.project(repo) == hostgit.Refused("config-key:filter.m.clean")
    assert len(spy.calls) == len(spy.listings) == 1
    assert _fired(markers) == []


@needs_git
def test_an_allowlisted_config_passes_and_no_driver_exists_to_fire(
        tmp_path, lab, spy):
    _ceiling, repo, markers, _drivers = lab
    assert _plain(repo, "config", "user.name", "T").returncode == 0
    _clear(markers)
    co = hostgit.project(repo)
    assert isinstance(co, hostgit.Checkout)
    assert hostgit.origin_url(repo) == "https://github.com/acme/widget.git"
    # A sealed spawn built the way run() will build it, on a repository whose
    # .gitattributes still names the drivers:
    (repo / "a.txt").write_text("edited\n", encoding="utf-8")
    argv = hostgit._argv("diff", "--no-ext-diff", "--no-textconv")
    done = hostgit._spawn(argv, hostgit._sealed_env(co), co.root, 30)
    assert done.returncode == 0, done.stderr
    assert _fired(markers) == []


# ---- the allowlist, as a table ----------------------------------------------

ACCEPTED = [
    "core.repositoryformatversion", "core.filemode", "core.bare",
    "core.logallrefupdates", "core.ignorecase", "core.precomposeunicode",
    "core.symlinks", "user.name", "user.email", "pull.rebase",
    "init.defaultbranch",
    "remote.origin.url", "remote.origin.fetch", "remote.origin.pushurl",
    "remote.up.stream.url",
    "branch.main.remote", "branch.main.merge", "branch.main.rebase",
    "branch.feat/x.merge",
]

REFUSED = [
    "include.path", "includeif.gitdir:/x.path", "extensions.worktreeconfig",
    "filter.x.clean", "diff.x.textconv", "merge.x.driver",
    "core.sshcommand", "core.pager", "core.fsmonitor", "core.hookspath",
    "credential.helper", "alias.x", "uploadpack.packobjectshook",
    "remote.origin.uploadpack", "url.x.insteadof", "gpg.program",
    # the rest of the issue's "refused by not being listed" families
    "core.editor", "core.askpass", "core.gitproxy", "core.alternaterefscommand",
    "core.worktree", "diff.external", "receive.denycurrentbranch",
    "remote.origin.receivepack", "remote.origin.proxy", "remote.origin.vcs",
    "sequence.editor", "difftool.x.cmd", "mergetool.x.cmd",
    "protocol.ext.allow", "transfer.fsckobjects", "fetch.fsckobjects",
    "http.proxy", "ssh.variant", "branch.main.description", "core.abbrev",
    "remote.url", "branch.merge", "user.signingkey",
]


def _config_text(key: str, value="1") -> str:
    section, _, rest = key.partition(".")
    sub, dot, name = rest.rpartition(".")
    if not dot:
        sub, name = "", rest
    head = f'[{section} "{sub}"]' if sub else f"[{section}]"
    return f"{head}\n\t{name} = {value}\n"


def _repo_with(ceiling: Path, key: str) -> Path:
    repo = fake_repo(ceiling / "r", url=None)
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8") + _config_text(key),
                   encoding="utf-8")
    return repo


@pytest.fixture
def home(tmp_path):
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    return ceiling


@needs_git
def test_the_allowlist_table_is_printed_whole():
    print("\nCONFIG_ALLOWLIST exact keys:")
    for key in hostgit.CONFIG_ALLOWLIST:
        print(f"  {key}")
    print("CONFIG_ALLOWLIST patterns:")
    for pattern in hostgit.CONFIG_ALLOWLIST_PATTERNS:
        print(f"  {pattern}")
    assert len(hostgit.CONFIG_ALLOWLIST_EXACT) == 11
    assert len(hostgit.CONFIG_ALLOWLIST_PATTERNS) == 6


@needs_git
@pytest.mark.parametrize("key", ACCEPTED)
def test_every_listed_key_and_pattern_passes(home, key):
    repo = _repo_with(home, key)
    assert isinstance(hostgit.project(repo), hostgit.Checkout)


@needs_git
@pytest.mark.parametrize("key", REFUSED)
def test_a_key_outside_the_allowlist_is_refused_with_its_code(home, key):
    repo = _repo_with(home, key)
    assert hostgit.project(repo) == hostgit.Refused(f"config-key:{key}")
    assert (str(repo), f"config-key:{key}") in hostgit.refusals()


def test_the_matcher_and_the_constant_agree():
    for key in ACCEPTED:
        assert hostgit.config_key_allowed(key), key
    for key in REFUSED:
        assert not hostgit.config_key_allowed(key), key
    assert not hostgit.config_key_allowed("")
    assert not hostgit.config_key_allowed("core")


# ---- files next to the config ------------------------------------------------


@pytest.mark.parametrize("relpath, code", [
    ("config.worktree", "worktree-config"),
    ("objects/info/alternates", "alternates"),
])
def test_worktree_config_and_alternates_refuse_without_a_listing(
        home, relpath, code, monkeypatch):
    repo = fake_repo(home / "r")
    target = repo / ".git" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[user]\n\tname = x\n", encoding="utf-8")
    rec = Recorder()
    monkeypatch.setattr(hostgit, "_spawn", rec)
    assert hostgit.project(repo) == hostgit.Refused(code)
    assert rec.calls == []
    assert (str(repo), code) in hostgit.refusals()


# ---- the listing spawn itself ---------------------------------------------------


def test_the_listing_runs_outside_the_repository_with_a_pinned_env(home,
                                                                  monkeypatch):
    repo = fake_repo(home / "r")
    rec = Recorder()
    monkeypatch.setattr(hostgit, "_spawn", rec)
    co = hostgit.project(repo)
    assert isinstance(co, hostgit.Checkout)
    (call,) = rec.calls
    cfg = str(repo.resolve() / ".git" / "config")
    assert call["argv"][:5] == ["git", "-c", "core.fsmonitor=false", "-c",
                                f"core.hooksPath={hostgit._hooks_dir()}"]
    assert call["argv"][5:] == ["config", "--file", cfg, "--no-includes",
                                "--list", "-z"]
    scratch = hostgit._scratch_root()
    env = call["env"]
    assert not {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"} & set(env)
    assert env["GIT_CEILING_DIRECTORIES"] == scratch
    for pin, want in (("GIT_CONFIG_NOSYSTEM", "1"),
                      ("GIT_CONFIG_GLOBAL", os.devnull),
                      ("GIT_ATTR_NOSYSTEM", "1"), ("GIT_TERMINAL_PROMPT", "0"),
                      ("GIT_OPTIONAL_LOCKS", "0")):
        assert env[pin] == want
    assert set(env) <= (set(hostgit._SEALED_PASS)
                        | {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                           "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
                           "GIT_OPTIONAL_LOCKS", "GIT_CEILING_DIRECTORIES"})
    assert call["cwd"] != str(repo.resolve())
    assert os.path.commonpath([call["cwd"], scratch]) == scratch
    assert os.listdir(call["cwd"]) == []  # dedicated and empty


@needs_git
def test_the_listing_works_on_a_valid_repo_from_the_scratch_cwd(lab, spy):
    _ceiling, repo, _markers, _drivers = lab
    assert isinstance(hostgit.project(repo), hostgit.Checkout)
    (call,) = spy.listings
    assert "GIT_DIR" not in call["env"]
    assert call["cwd"] != str(repo.resolve())


@needs_git
def test_a_poisoned_ambient_git_dir_cannot_redirect_the_listing(lab, monkeypatch):
    _ceiling, repo, _markers, drivers = lab
    _arm_directly(repo, drivers)
    elsewhere = repo.parent / "other"
    elsewhere.mkdir()
    assert _plain(elsewhere, "init", "-q").returncode == 0
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert hostgit.project(repo) == hostgit.Refused("config-key:filter.m.clean")


@pytest.mark.parametrize("how", ["rc", "timeout", "oserror"])
def test_a_listing_that_fails_refuses_as_unreadable(home, monkeypatch, how):
    repo = fake_repo(home / "r")
    rec = Recorder()
    if how == "rc":
        rec.answer("", rc=128, stderr="fatal: bad config")
    elif how == "timeout":
        rec.raises = subprocess.TimeoutExpired("git", 2)
    else:
        rec.raises = FileNotFoundError("git")
    monkeypatch.setattr(hostgit, "_spawn", rec)
    assert hostgit.project(repo) == hostgit.Refused("config-unreadable")
    assert len(rec.calls) == 1


def test_the_first_offending_key_in_file_order_names_the_refusal(home,
                                                                 monkeypatch):
    repo = fake_repo(home / "r")
    rec = Recorder()
    rec.answer("core.filemode\ntrue\0user.name\nT\0core.sshcommand\nx\0"
               "filter.m.clean\ny\0")
    monkeypatch.setattr(hostgit, "_spawn", rec)
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.sshcommand")


def test_a_valueless_key_is_still_a_key(home, monkeypatch):
    # `-z` prints `key\0` (no newline) for a boolean-by-presence entry.
    repo = fake_repo(home / "r")
    rec = Recorder()
    rec.answer("core.filemode\ntrue\0core.pager\0")
    monkeypatch.setattr(hostgit, "_spawn", rec)
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")


@needs_git
def test_a_config_that_does_not_parse_fails_closed(home):
    repo = fake_repo(home / "r")
    cfg = repo / ".git" / "config"
    cfg.write_text("[core\n\trepositoryformatversion = 0\n", encoding="utf-8")
    assert hostgit.project(repo) == hostgit.Refused("config-unreadable")


@needs_git
def test_a_malformed_value_of_an_allowed_key_is_not_the_gates_to_judge(home):
    # Listed from outside the repository, git does not validate values; the
    # key is allowed, and every later sealed git call fails on the value itself.
    repo = fake_repo(home / "r")
    cfg = repo / ".git" / "config"
    cfg.write_text("[core]\n\trepositoryformatversion = banana\n",
                   encoding="utf-8")
    assert isinstance(hostgit.project(repo), hostgit.Checkout)


# ---- memo ---------------------------------------------------------------------------


@needs_git
def test_an_unchanged_config_is_listed_once_and_a_changed_one_again(home, spy):
    repo = fake_repo(home / "r")
    assert isinstance(hostgit.project(repo), hostgit.Checkout)
    assert isinstance(hostgit.project(repo), hostgit.Checkout)
    assert len(spy.listings) == 1
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + '[core]\n\tpager = /bin/evil\n', encoding="utf-8")
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")
    assert len(spy.listings) == 2


@needs_git
def test_a_refusal_verdict_is_memoized_too_and_reset_clears_the_memo(home, spy):
    repo = _repo_with(home, "core.pager")
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")
    assert len(spy.listings) == 1
    hostgit._reset()
    hostgit.configure(posture=SEALED, ceiling=home)
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")
    assert len(spy.listings) == 2


@needs_git
def test_a_config_swapped_for_a_different_file_of_the_same_size_regates(home,
                                                                       spy):
    repo = fake_repo(home / "r", url=None)
    cfg = repo / ".git" / "config"
    assert isinstance(hostgit.project(repo), hostgit.Checkout)
    stat = cfg.stat()
    evil = "[core]\n\tpager = "
    evil += "a" * (stat.st_size - len(evil) - 1) + "\n"
    assert len(evil) == stat.st_size
    swap = cfg.with_name("config.new")  # a fresh inode, same size, same mtime
    swap.write_text(evil, encoding="utf-8")
    os.utime(swap, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    swap.replace(cfg)
    assert hostgit.project(repo) == hostgit.Refused("config-key:core.pager")
    assert len(spy.listings) == 2


# ---- ambient posture is untouched ----------------------------------------------------


@needs_git
def test_ambient_never_runs_the_gate(tmp_path, spy):
    repo = _repo_with(tmp_path, "core.pager")
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    hostgit.project(repo)
    assert spy.listings == []
