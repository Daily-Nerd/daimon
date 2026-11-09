"""hostgit.run and hostgit.init: sealed plumbing on a gated checkout (#1308, slice 2).

Recorder tests assert the argv, env and cwd built here; real-git tests prove no
marker script fires. Tests use tmp dirs only.
"""

import os
import shutil
import subprocess

import pytest

from daimon_briefing import hostgit
from tests.test_hostgit_config_gate import (  # noqa: F401  (fixtures)
    Spy, _arm_directly, _clear, _exercise_plain, _fired, _marker_script,
    _plain, lab, needs_git, spy)
from tests.test_hostgit_sealed import SEALED, SealedRecorder, fake_repo

PINS = {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS"}

DIFF_FLAGS = ("--no-ext-diff", "--no-textconv", "--ignore-submodules=all")
DIFF_FAMILY = ("diff", "show", "log", "diff-tree", "diff-index", "diff-files")
PLAIN_FAMILY = ("rev-parse", "ls-files", "ls-tree", "cat-file", "symbolic-ref",
                "for-each-ref", "rev-list", "add")


@pytest.fixture
def home(tmp_path):
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    return ceiling


@pytest.fixture
def rec(monkeypatch):
    recorder = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", recorder)
    return recorder


@pytest.fixture
def co(home, rec):
    got = hostgit.project(fake_repo(home / "r"))
    assert isinstance(got, hostgit.Checkout)
    rec.listings.clear()
    return got


# ---- the table ------------------------------------------------------------


def test_the_family_table_is_printed_whole_and_has_the_expected_rows():
    print("\nRUN_FAMILIES (subcommand: options before it, flags after it):")
    for name, fam in sorted(hostgit.RUN_FAMILIES.items()):
        print(f"  {name}: options={list(fam.options)} flags={list(fam.flags)}")
    assert set(hostgit.RUN_FAMILIES) == {
        "diff", "show", "log", "diff-tree", "diff-index", "diff-files",
        "status", "init", *PLAIN_FAMILY}
    for name in DIFF_FAMILY:
        assert hostgit.RUN_FAMILIES[name].flags == DIFF_FLAGS
        assert hostgit.RUN_FAMILIES[name].options == ("-c", "diff.external=")
    # git status rejects --no-ext-diff and --no-textconv (git 2.50.1)
    assert hostgit.RUN_FAMILIES["status"].flags == ("--ignore-submodules=all",)
    assert hostgit.RUN_FAMILIES["status"].options == ()
    assert hostgit.RUN_FAMILIES["init"].flags == ("--template=",)
    for name in PLAIN_FAMILY:
        assert hostgit.RUN_FAMILIES[name].flags == ()
        assert hostgit.RUN_FAMILIES[name].options == ()


# ---- argv, env, cwd per family ----------------------------------------------


@pytest.mark.parametrize("sub", sorted(hostgit.RUN_FAMILIES))
def test_run_builds_the_sealed_argv_env_and_cwd(co, rec, sub):
    fam = hostgit.RUN_FAMILIES[sub]
    hostgit.run(co, sub, "extra")
    (call,) = rec.calls
    assert call["argv"] == ["git", *hostgit._hardening(), *fam.options, sub,
                            *fam.flags, "extra"]
    assert call["argv"][:5] == ["git", "-c", "core.fsmonitor=false", "-c",
                                f"core.hooksPath={hostgit._hooks_dir()}"]
    assert call["env"] == hostgit._sealed_env(co)
    assert {k for k in call["env"] if k.startswith("GIT_")} == PINS
    assert call["cwd"] == co.root
    assert call["timeout"] == hostgit.PROJECT_TIMEOUT
    assert call["stdin"] == subprocess.DEVNULL


def test_run_takes_a_timeout_and_a_hang_becomes_rc_124(co, rec):
    rec.raises = subprocess.TimeoutExpired("git", 9)
    done = hostgit.run(co, "status", timeout=9)
    assert rec.calls[0]["timeout"] == 9
    assert done.returncode == 124 and "timed out" in done.stderr


def test_a_poisoned_process_environment_stays_out_of_the_child(co, rec,
                                                              monkeypatch):
    for key in ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_SSH_COMMAND",
                "GIT_TEMPLATE_DIR", "GIT_CONFIG_COUNT"):
        monkeypatch.setenv(key, "/poison")
    hostgit.run(co, "diff")
    env = rec.calls[0]["env"]
    assert not {"GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_SSH_COMMAND",
                "GIT_TEMPLATE_DIR", "GIT_CONFIG_COUNT"} & set(env)


# ---- what run() refuses to be asked --------------------------------------------


def test_run_under_ambient_raises_before_any_spawn(tmp_path, monkeypatch):
    rec = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", rec)
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    with pytest.raises(RuntimeError, match="sealed"):
        hostgit.run(hostgit.Checkout(root=str(tmp_path), gitdir=str(tmp_path)),
                    "status")
    assert rec.calls == [] and rec.listings == []


@pytest.mark.parametrize("bad", [
    hostgit.Refused("symlink"), "/some/path", object(), None, ("a", "b")])
def test_run_wants_a_checkout_not_a_refusal_or_a_path(home, rec, bad):
    with pytest.raises(TypeError):
        hostgit.run(bad, "status")
    assert rec.calls == [] and rec.listings == []


def test_run_wants_a_checkout_sealed_project_returned(home, rec, tmp_path):
    with pytest.raises(TypeError):
        hostgit.run(hostgit.Checkout(root=str(tmp_path)), "status")  # gitdir None
    assert rec.calls == [] and rec.listings == []


def test_a_path_object_is_not_a_checkout_either(home, rec, tmp_path):
    with pytest.raises(TypeError):
        hostgit.run(tmp_path, "status")


@pytest.mark.parametrize("sub", ["push", "config", "-c", "--version", "fetch",
                                 "clone", "", "alias", "STATUS", "-C"])
def test_a_subcommand_outside_the_table_raises_before_any_spawn(co, rec, sub):
    with pytest.raises(ValueError, match="RUN_FAMILIES"):
        hostgit.run(co, sub, "x")
    assert rec.calls == [] and rec.listings == []


def test_run_with_no_arguments_is_a_value_error(co, rec):
    with pytest.raises(ValueError, match="RUN_FAMILIES"):
        hostgit.run(co)
    assert rec.calls == []


# ---- re-gate --------------------------------------------------------------------


@needs_git
def test_a_config_rewritten_after_project_refuses_run(home, spy):  # noqa: F811
    repo = fake_repo(home / "r")
    co = hostgit.project(repo)
    assert isinstance(co, hostgit.Checkout)
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + '[filter "m"]\n\tclean = /bin/evil\n', encoding="utf-8")
    spy.calls.clear()
    done = hostgit.run(co, "status")
    assert done.returncode == 125
    assert "config-key:filter.m.clean" in done.stderr
    assert len(spy.calls) == 1 and spy.calls == spy.listings  # the listing only
    assert (co.root, "config-key:filter.m.clean") in hostgit.refusals()


def test_a_checkout_whose_gitdir_differs_from_the_answer_is_refused(co, rec):
    forged = hostgit.Checkout(root=co.root, gitdir=co.gitdir + "-other")
    done = hostgit.run(forged, "status")
    assert done.returncode == 125 and "checkout-changed" in done.stderr
    assert rec.calls == []
    assert (co.root, "checkout-changed") in hostgit.refusals()


def test_a_checkout_that_stopped_being_a_repo_is_refused(home, rec, co):
    shutil.rmtree(os.path.join(co.gitdir, "refs"))
    done = hostgit.run(co, "status")
    assert done.returncode == 125 and "empty-gitdir" in done.stderr
    assert rec.calls == []


# ---- real git: markers --------------------------------------------------------------


@needs_git
def test_run_fires_no_driver_on_an_allowlisted_repo_and_answers_correctly(
        tmp_path, lab, monkeypatch):  # noqa: F811
    _ceiling, repo, markers, _drivers = lab
    # Environment-level routes to the same programs, none of which may reach
    # the child.
    ext = _marker_script(tmp_path / "ext-env", markers, "external-env")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(ext))
    co = hostgit.project(repo)
    assert isinstance(co, hostgit.Checkout)
    _clear(markers)
    (repo / "a.txt").write_text("edited\n", encoding="utf-8")

    diff = hostgit.run(co, "diff", timeout=30)
    assert diff.returncode == 0, diff.stderr
    assert "-a" in diff.stdout and "+edited" in diff.stdout
    status = hostgit.run(co, "status", "--porcelain", timeout=30)
    assert status.stdout.strip() == "M a.txt"
    assert hostgit.run(co, "add", "--", "a.txt", timeout=30).returncode == 0
    cached = hostgit.run(co, "diff", "--cached", "--stat", timeout=30)
    assert "a.txt" in cached.stdout
    show = hostgit.run(co, "show", "--stat", "--format=%s", "HEAD", timeout=30)
    assert show.returncode == 0 and "seed" in show.stdout
    assert hostgit.run(co, "ls-files", timeout=30).stdout.split() == [
        ".gitattributes", "a.txt"]
    assert hostgit.run(co, "rev-parse", "--abbrev-ref", "HEAD",
                       timeout=30).stdout.strip() == "main"
    assert _fired(markers) == []


@needs_git
def test_control_plain_git_fires_what_run_is_never_asked_to_serve(tmp_path, lab):  # noqa: F811
    _ceiling, repo, markers, drivers = lab
    _arm_directly(repo, drivers)
    _clear(markers)
    _exercise_plain(repo)
    assert {"clean", "textconv", "external"} <= set(_fired(markers))
    # ...and run() cannot even be obtained on that repository.
    _clear(markers)
    assert hostgit.project(repo) == hostgit.Refused("config-key:filter.m.clean")
    assert _fired(markers) == []


# ---- init -------------------------------------------------------------------------------


def test_init_builds_a_template_free_argv_outside_any_repo(home, rec):
    target = home / "new"
    target.mkdir()
    got = hostgit.init(target)
    (call,) = rec.calls
    assert call["argv"] == ["git", *hostgit._hardening(), "init", "-q",
                            "--template="]
    assert not {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"} & set(call["env"])
    assert call["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
    assert call["cwd"] == str(target.resolve())
    assert isinstance(got, hostgit.Refused)  # the recorder made no repository


def test_init_under_ambient_raises(tmp_path, monkeypatch):
    rec = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", rec)
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    with pytest.raises(RuntimeError, match="sealed"):
        hostgit.init(tmp_path)
    assert rec.calls == []


@pytest.mark.parametrize("build, code", [
    (lambda c: c / "missing", "not-a-dir"),
    (lambda c: c, "outside-ceiling"),
])
def test_init_refuses_outside_the_ceiling_and_missing_dirs(home, rec, build,
                                                           code):
    assert hostgit.init(build(home)) == hostgit.Refused(code)
    assert rec.calls == []


def test_init_never_runs_over_an_existing_dot_git_entry(home, rec):
    target = home / "p"
    target.mkdir()
    (target / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert hostgit.init(target) == hostgit.Refused("gitfile")
    assert rec.calls == []


@needs_git
def test_init_creates_a_gated_repo_with_no_template_hooks(tmp_path, home,
                                                          monkeypatch):
    tpl = tmp_path / "tpl"
    (tpl / "hooks").mkdir(parents=True)
    _marker_script(tpl / "hooks" / "pre-commit", tmp_path, "tpl-hook")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tpl))
    # control: plain git honors the template and installs the hook
    plain = tmp_path / "plain"
    plain.mkdir()
    env = {**os.environ, "GIT_TEMPLATE_DIR": str(tpl)}
    subprocess.run(["git", "init", "-q"], cwd=plain, env=env, check=True,
                   capture_output=True)
    assert (plain / ".git" / "hooks" / "pre-commit").exists()

    target = home / "fresh"
    target.mkdir()
    got = hostgit.init(target)
    assert isinstance(got, hostgit.Checkout)
    assert got.gitdir == str(target.resolve() / ".git")
    hooks = target / ".git" / "hooks"
    assert not hooks.exists() or list(hooks.iterdir()) == []
    assert hostgit.project(target) == got
    assert hostgit.run(got, "status", "--porcelain", timeout=30).returncode == 0


@pytest.mark.parametrize("how", ["rc", "oserror", "timeout"])
def test_init_that_fails_is_refused_as_init_failed(home, rec, how):
    target = home / "new"
    target.mkdir()
    if how == "rc":
        rec.answer("", rc=128, stderr="fatal: cannot init")
    elif how == "oserror":
        rec.raises = FileNotFoundError("git")
    else:
        rec.raises = subprocess.TimeoutExpired("git", 15)
    assert hostgit.init(target) == hostgit.Refused("init-failed")
    assert len(rec.calls) == 1 and rec.listings == []
    assert (str(target), "init-failed") in hostgit.refusals()
