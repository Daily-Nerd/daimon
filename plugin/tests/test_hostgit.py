"""hostgit: the one module that talks to git and gh (AMBIENT posture).

Every test here replaces the private spawn seam with a recorder, so what is
asserted is the argv, env, cwd and timeout hostgit builds, not what a real git
answers. Real-git behaviour is covered by the suites of the callers (config,
teamproject, worldcheck, teamsync), which run unchanged on top of hostgit.
"""

import os
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import hostgit


class Recorder:
    """Stands in for `hostgit._spawn` / `hostgit._spawn_async`."""

    def __init__(self):
        self.calls: list[dict] = []
        self.result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        self.raises: BaseException | None = None

    def __call__(self, argv, env, cwd, timeout, stdin=None):
        self.calls.append({"argv": list(argv), "env": env, "cwd": cwd,
                           "timeout": timeout, "stdin": stdin})
        if self.raises is not None:
            raise self.raises
        return self.result

    def answer(self, stdout="", rc=0, stderr=""):
        self.result = subprocess.CompletedProcess([], rc, stdout=stdout,
                                                  stderr=stderr)


@pytest.fixture
def rec(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(hostgit, "_spawn", recorder)
    # The recorder tests name made-up paths; ownership has its own tests.
    monkeypatch.setattr(hostgit, "_guard_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(hostgit, "_guard_clone", lambda *a, **k: None)
    return recorder


def H(*, sidecar=False):
    """The hardening options every git argv carries."""
    return hostgit._hardening(sidecar=sidecar)


# ---- posture --------------------------------------------------------------


def test_default_posture_is_ambient_with_nothing_set(monkeypatch):
    monkeypatch.delenv(hostgit.POSTURE_ENV, raising=False)
    assert hostgit.posture() is hostgit.Posture.AMBIENT


def test_env_selects_the_posture(monkeypatch):
    monkeypatch.setenv(hostgit.POSTURE_ENV, "sealed")
    assert hostgit.posture() is hostgit.Posture.SEALED
    monkeypatch.setenv(hostgit.POSTURE_ENV, "AMBIENT")
    assert hostgit.posture() is hostgit.Posture.AMBIENT


def test_an_unknown_env_value_fails_loudly_not_open(monkeypatch):
    monkeypatch.setenv(hostgit.POSTURE_ENV, "paranoid")
    with pytest.raises(ValueError, match=hostgit.POSTURE_ENV):
        hostgit.posture()


def test_configure_beats_env_and_none_returns_to_env(monkeypatch):
    monkeypatch.setenv(hostgit.POSTURE_ENV, "sealed")
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    assert hostgit.posture() is hostgit.Posture.AMBIENT
    hostgit.configure(posture=None)
    assert hostgit.posture() is hostgit.Posture.SEALED


def test_sealed_is_not_built_yet_and_says_so(rec, monkeypatch):
    with pytest.raises(NotImplementedError, match="SEALED"):
        hostgit.configure(posture="sealed")
    # Selected through the env, every entry point refuses instead of
    # silently answering as AMBIENT would.
    monkeypatch.setenv(hostgit.POSTURE_ENV, "sealed")
    for call in (lambda: hostgit.project("/p"), lambda: hostgit.origin_url("/p"),
                 lambda: hostgit.branch("/p"), lambda: hostgit.user_name("/p"),
                 lambda: hostgit.remote_listing("/p"),
                 lambda: hostgit.sidecar("/p", "status"),
                 lambda: hostgit.has_entry("/p"), lambda: hostgit.refs_dir("/p")):
        with pytest.raises(NotImplementedError):
            call()
    assert rec.calls == []


def test_reset_clears_a_configured_posture():
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    hostgit._reset()
    assert hostgit._configured is None


# ---- argv is built in one place -------------------------------------------

PROJECT_CASES = [
    # (label, call, expected argv, expected timeout, expected cwd)
    ("project", lambda: hostgit.project("/p"),
     ["git", *H(), "-C", "/p", "rev-parse", "--show-toplevel"], 2, None),
    ("branch", lambda: hostgit.branch("/p"),
     ["git", *H(), "-C", "/p", "rev-parse", "--abbrev-ref", "HEAD"], 2, None),
    ("origin_url", lambda: hostgit.origin_url("/p"),
     ["git", *H(), "-C", "/p", "remote", "get-url", "origin"], 5, None),
    ("remote_listing", lambda: hostgit.remote_listing("/p"),
     ["git", *H(), "-C", "/p", "remote", "-v"], 2, None),
    ("user_name", lambda: hostgit.user_name("/cwd"),
     ["git", *H(), "config", "user.name"], 2, "/cwd"),
]


@pytest.mark.parametrize("label,call,argv,timeout,cwd", PROJECT_CASES,
                         ids=[c[0] for c in PROJECT_CASES])
def test_project_reads_spawn_exactly_the_argv_they_always_did(
        rec, label, call, argv, timeout, cwd):
    rec.answer("x\n")
    call()
    assert len(rec.calls) == 1
    got = rec.calls[0]
    assert got["argv"] == argv
    assert got["timeout"] == timeout
    assert got["cwd"] == cwd
    assert got["stdin"] is None


def test_origin_url_timeout_is_the_callers(rec):
    rec.answer("u\n")
    hostgit.origin_url("/p", timeout=9)
    assert rec.calls[0]["timeout"] == 9


def test_pathlike_inputs_become_strings_in_argv(rec, tmp_path):
    rec.answer("x\n")
    hostgit.origin_url(tmp_path)
    assert rec.calls[0]["argv"][1 + len(H()) + 1] == str(tmp_path)


def test_sidecar_argv_pins_cwd_with_dash_c_and_passes_args_through(rec):
    hostgit.sidecar("/s", "status", "--porcelain", "--", "a", timeout=7)
    got = rec.calls[0]
    assert got["argv"] == ["git", *H(sidecar=True), "-C", "/s", "status",
                           "--porcelain", "--", "a"]
    assert got["timeout"] == 7
    assert got["stdin"] is subprocess.DEVNULL


def test_sidecar_leading_options_sit_between_dash_c_and_the_command(rec):
    hostgit.sidecar("/s", "commit", "-m", "m", options=["-c", "user.name=A"])
    assert rec.calls[0]["argv"] == [
        "git", *H(sidecar=True), "-C", "/s", "-c", "user.name=A", "commit",
        "-m", "m"]


def test_sidecar_default_timeout_is_the_local_plumbing_budget(rec):
    hostgit.sidecar("/s", "status")
    assert rec.calls[0]["timeout"] == hostgit.SIDECAR_TIMEOUT == 15


def test_clone_has_no_dash_C_and_ends_options_before_the_url(rec):
    hostgit.clone("https://h/r.git", "/dest", timeout=60)
    got = rec.calls[0]
    assert got["argv"] == ["git", *H(sidecar=True), "clone", "--",
                           "https://h/r.git", "/dest"]
    assert got["timeout"] == 60


# ---- env is os.environ-derived in AMBIENT ---------------------------------


def test_ambient_project_env_is_the_process_environment_unchanged(rec, monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/ambient/dir")
    monkeypatch.setenv("GIT_WORK_TREE", "/ambient/tree")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", "/ceil")
    rec.answer("x\n")
    hostgit.project("/p")
    env = rec.calls[0]["env"]
    assert env == dict(os.environ)
    assert env["GIT_DIR"] == "/ambient/dir"  # #948: ambient pins still honoured
    assert env is not os.environ  # a copy, never the live mapping


def test_sidecar_env_is_the_process_environment_plus_no_prompt(rec, monkeypatch):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i k")
    hostgit.sidecar("/s", "fetch", "origin")
    env = rec.calls[0]["env"]
    assert env == {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def test_clone_env_is_the_sidecar_env(rec):
    hostgit.clone("u", "/d", timeout=1)
    assert rec.calls[0]["env"] == {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    assert rec.calls[0]["stdin"] is subprocess.DEVNULL


# ---- answers --------------------------------------------------------------


def test_project_returns_a_checkout_and_leaves_gitdir_to_git_in_ambient(rec):
    rec.answer("/repo\n")
    got = hostgit.project("/repo/sub")
    assert got == hostgit.Checkout(root="/repo", gitdir=None)


@pytest.mark.parametrize("answer", [dict(rc=128), dict(rc=0, stdout="  \n")])
def test_project_refuses_without_an_answer(rec, answer):
    rec.answer(**answer)
    got = hostgit.project("/p")
    assert got == hostgit.Refused("git-failed")


@pytest.mark.parametrize("exc", [FileNotFoundError("git"), OSError("x"),
                                 subprocess.TimeoutExpired("git", 2),
                                 ValueError("embedded null byte")])
def test_every_project_read_swallows_spawn_failures(rec, exc):
    rec.raises = exc
    assert hostgit.project("/p") == hostgit.Refused("git-failed")
    assert hostgit.branch("/p") is None
    assert hostgit.origin_url("/p") is None
    assert hostgit.remote_listing("/p") is None
    assert hostgit.user_name("/p") == ""


def test_branch_is_none_for_detached_empty_and_failed(rec):
    rec.answer("feature/x\n")
    assert hostgit.branch("/p") == "feature/x"
    rec.answer("HEAD\n")
    assert hostgit.branch("/p") is None
    rec.answer("\n")
    assert hostgit.branch("/p") is None
    rec.answer("feature/x\n", rc=128)
    assert hostgit.branch("/p") is None


def test_origin_url_and_user_name_strip_and_fail_to_the_empty_value(rec):
    rec.answer("git@h:o/r.git\n")
    assert hostgit.origin_url("/p") == "git@h:o/r.git"
    rec.answer("\n")
    assert hostgit.origin_url("/p") is None
    rec.answer("Ada \n")
    assert hostgit.user_name("/p") == "Ada"
    rec.answer("Ada\n", rc=1)
    assert hostgit.user_name("/p") == ""


def test_remote_listing_keeps_the_difference_between_empty_and_failed(rec):
    rec.answer("")
    assert hostgit.remote_listing("/p") == ""
    rec.answer("", rc=128)
    assert hostgit.remote_listing("/p") is None


def test_a_hung_sidecar_command_becomes_a_failed_process_not_a_raise(rec):
    rec.raises = subprocess.TimeoutExpired("git", 3)
    proc = hostgit.sidecar("/s", "fetch", timeout=3)
    assert proc.returncode == 124
    assert "timed out after 3s" in proc.stderr
    assert hostgit.clone("u", "/d", timeout=3).returncode == 124


# ---- gh and the rest of the project-directory surface ---------------------


def test_gh_popen_spawns_with_the_project_as_cwd_and_the_ambient_env(monkeypatch):
    seen = {}

    def fake(argv, env, cwd, stdin):
        seen.update(argv=argv, env=env, cwd=cwd, stdin=stdin)
        return "proc"

    monkeypatch.setattr(hostgit, "_spawn_async", fake)
    got = hostgit.gh_popen(["/bin/gh", "pr", "view", "7"], "/proj")
    assert got == "proc"
    assert seen == {"argv": ["/bin/gh", "pr", "view", "7"],
                    "env": dict(os.environ), "cwd": "/proj", "stdin": subprocess.DEVNULL}


def test_has_entry_and_refs_dir_read_the_dot_git_entry(tmp_path):
    assert hostgit.has_entry(tmp_path) is False
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    assert hostgit.has_entry(tmp_path) is True
    assert hostgit.refs_dir(tmp_path) == gitdir.resolve()


def test_refs_dir_follows_a_gitfile_to_the_common_dir(tmp_path):
    common = tmp_path / "main" / ".git"
    (common / "refs" / "heads").mkdir(parents=True)
    wt_git = common / "worktrees" / "w"
    wt_git.mkdir(parents=True)
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    assert hostgit.refs_dir(wt) == common.resolve()


def test_refs_dir_is_none_without_refs_heads_or_with_a_junk_gitfile(tmp_path):
    (tmp_path / ".git").mkdir()
    assert hostgit.refs_dir(tmp_path) is None
    other = tmp_path / "o"
    other.mkdir()
    (other / ".git").write_text("nonsense\n", encoding="utf-8")
    assert hostgit.refs_dir(other) is None


def test_gh_path_and_git_available_ask_shutil_which(monkeypatch):
    asked = []

    def which(name):
        asked.append(name)
        return f"/bin/{name}" if name == "gh" else None

    monkeypatch.setattr(hostgit.shutil, "which", which)
    assert hostgit.gh_path() == "/bin/gh"
    assert hostgit.git_available() is False
    assert asked == ["gh", "git"]


def test_checkout_and_refused_are_plain_frozen_values():
    c = hostgit.Checkout(root="/r")
    assert c.gitdir is None
    with pytest.raises(Exception):
        c.root = "/x"  # type: ignore[misc]
    assert Path(hostgit.__file__).name == "hostgit.py"


# ---- every public git function carries the hardening options ---------------

EVERY_CALL = [
    ("project", lambda: hostgit.project("/p"), False),
    ("origin_url", lambda: hostgit.origin_url("/p"), False),
    ("branch", lambda: hostgit.branch("/p"), False),
    ("remote_listing", lambda: hostgit.remote_listing("/p"), False),
    ("user_name", lambda: hostgit.user_name("/p"), False),
    ("sidecar", lambda: hostgit.sidecar("/s", "status"), True),
    ("sidecar_options", lambda: hostgit.sidecar(
        "/s", "commit", options=["-c", "user.name=A"]), True),
    ("clone", lambda: hostgit.clone("u", "/d", timeout=1), True),
]


@pytest.mark.parametrize("label,call,is_sidecar", EVERY_CALL,
                         ids=[c[0] for c in EVERY_CALL])
def test_every_git_argv_carries_fsmonitor_and_hooks_off_before_the_subcommand(
        rec, label, call, is_sidecar):
    rec.answer("x\n")
    call()
    argv = rec.calls[0]["argv"]
    assert argv[0] == "git"
    assert argv[1:5] == ["-c", "core.fsmonitor=false", "-c",
                         f"core.hooksPath={hostgit._hooks_dir()}"]
    assert ("protocol.ext.allow=never" in argv) is is_sidecar
    first_plain = next(i for i, a in enumerate(argv[1:], 1)
                       if not a.startswith("-") and argv[i - 1] != "-c"
                       and argv[i - 1] != "-C")
    assert argv.index("core.fsmonitor=false") < first_plain


def test_the_hooks_directory_is_one_private_empty_directory():
    path = Path(hostgit._hooks_dir())
    assert path.is_dir() and list(path.iterdir()) == []
    assert (path.stat().st_mode & 0o777) == 0o700
    assert hostgit._hooks_dir() == str(path)  # one per process
