"""Real git against a sidecar whose repo config names programs to run.

The sidecar's own config sets `core.fsmonitor` and `core.hooksPath` (and a
hooks directory holding every hook) to scripts that drop a marker file. Run
through `hostgit.sidecar` none of them may fire; run through plain git, they do,
which is what makes the absence mean something. Tests use tmp dirs only.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import hostgit

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

HOOKS = ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
         "pre-merge-commit", "post-merge", "post-checkout", "pre-push",
         "reference-transaction", "pre-auto-gc", "post-rewrite")


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=60, env=env)


def _script(path: Path, markers: Path, name: str) -> Path:
    path.write_text(f"#!/bin/sh\ntouch '{markers}/{name}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def armed(tmp_path):
    """(sidecar, remote, markers): a sidecar clone whose config would run
    marker scripts, with one commit already on its branch."""
    markers = tmp_path / "markers"
    markers.mkdir()
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    sidecar.chmod(0o755)
    for step in (("init", "-q", "-b", "main"),
                 ("config", "user.name", "T"), ("config", "user.email", "t@x"),
                 ("remote", "add", "origin", str(remote))):
        assert _git(sidecar, *step).returncode == 0
    (sidecar / "a.txt").write_text("a\n", encoding="utf-8")
    assert _git(sidecar, "add", "a.txt").returncode == 0
    assert _git(sidecar, "commit", "-q", "-m", "seed").returncode == 0
    assert _git(sidecar, "push", "-q", "-u", "origin", "main").returncode == 0
    # Arm the config only after the seed history exists.
    hooks = tmp_path / "armed-hooks"
    hooks.mkdir()
    for name in HOOKS:
        _script(hooks / name, markers, name)
    fsmon = _script(tmp_path / "fsmon", markers, "fsmonitor")
    assert _git(sidecar, "config", "core.hooksPath", str(hooks)).returncode == 0
    assert _git(sidecar, "config", "core.fsmonitor", str(fsmon)).returncode == 0
    for m in markers.iterdir():
        m.unlink()
    return sidecar, remote, markers


def _fired(markers: Path) -> list[str]:
    return sorted(p.name for p in markers.iterdir())


def test_the_armed_config_does_fire_under_plain_git(armed):
    # The control: without it, "no marker" below proves nothing.
    sidecar, _remote, markers = armed
    (sidecar / "b.txt").write_text("b\n", encoding="utf-8")
    _git(sidecar, "status", "--porcelain")
    _git(sidecar, "add", "b.txt")
    _git(sidecar, "commit", "-q", "-m", "b", "--", "b.txt")
    fired = _fired(markers)
    assert "fsmonitor" in fired
    assert "pre-commit" in fired


def test_status_add_commit_fire_nothing_through_hostgit(armed):
    sidecar, _remote, markers = armed
    (sidecar / "b.txt").write_text("b\n", encoding="utf-8")
    assert hostgit.sidecar(sidecar, "status", "--porcelain",
                           "--untracked-files=all").returncode == 0
    assert hostgit.sidecar(sidecar, "add", "--", "b.txt").returncode == 0
    assert hostgit.sidecar(sidecar, "diff", "--cached", "--name-only").returncode == 0
    done = hostgit.sidecar(sidecar, "commit", "-m", "b", "--", "b.txt")
    assert done.returncode == 0, done.stderr
    assert _fired(markers) == []


def test_checkout_fetch_push_fire_nothing_through_hostgit(armed):
    sidecar, _remote, markers = armed
    (sidecar / "c.txt").write_text("c\n", encoding="utf-8")
    hostgit.sidecar(sidecar, "add", "--", "c.txt")
    assert hostgit.sidecar(sidecar, "commit", "-m", "c", "--", "c.txt").returncode == 0
    assert hostgit.sidecar(sidecar, "push", "-u", "origin", "main").returncode == 0
    assert hostgit.sidecar(sidecar, "fetch", "origin").returncode == 0
    assert hostgit.sidecar(sidecar, "checkout", "-B", "main",
                           "refs/remotes/origin/main").returncode == 0
    assert _fired(markers) == []


def test_merge_fires_nothing_through_hostgit(armed):
    sidecar, _remote, markers = armed
    assert _git(sidecar, "checkout", "-q", "-b", "side").returncode == 0
    (sidecar / "s.txt").write_text("s\n", encoding="utf-8")
    _git(sidecar, "add", "s.txt")
    # (plain git fires the armed hooks here; clear them before the measured run)
    _git(sidecar, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "s")
    assert _git(sidecar, "checkout", "-q", "main").returncode == 0
    (sidecar / "m.txt").write_text("m\n", encoding="utf-8")
    _git(sidecar, "add", "m.txt")
    _git(sidecar, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "m")
    for m in markers.iterdir():
        m.unlink()
    done = hostgit.sidecar(sidecar, "merge", "--no-edit", "side")
    assert done.returncode == 0, done.stderr
    assert _fired(markers) == []


def test_project_reads_fire_nothing_through_hostgit(armed):
    sidecar, _remote, markers = armed
    assert hostgit.project(sidecar) == hostgit.Checkout(
        root=str(sidecar.resolve()))
    assert hostgit.branch(sidecar) == "main"
    assert hostgit.origin_url(sidecar)
    assert hostgit.remote_listing(sidecar)
    assert _fired(markers) == []


# ---- sidecar ownership (test 18) ------------------------------------------


def _fresh(tmp_path):
    sidecar = tmp_path / "sc"
    sidecar.mkdir()
    sidecar.chmod(0o755)
    subprocess.run(["git", "-C", str(sidecar), "init", "-q"], check=True,
                   capture_output=True)
    return sidecar


def test_an_owned_private_sidecar_passes(tmp_path):
    sidecar = _fresh(tmp_path)
    assert hostgit.sidecar_problem(sidecar) is None
    assert hostgit.is_sidecar(sidecar) is True


def test_a_symlinked_sidecar_is_not_a_sidecar(tmp_path):
    real = _fresh(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert hostgit.is_sidecar(link) is False
    assert "symlink" in hostgit.sidecar_problem(link)


def test_a_symlinked_dot_git_is_not_a_sidecar(tmp_path):
    real = _fresh(tmp_path)
    other = tmp_path / "o"
    other.mkdir()
    (other / ".git").symlink_to(real / ".git", target_is_directory=True)
    assert hostgit.is_sidecar(other) is False
    assert "symlink" in hostgit.sidecar_problem(other)


def test_a_dot_git_file_is_not_a_sidecar(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    d.chmod(0o755)
    (d / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert "not a directory" in hostgit.sidecar_problem(d)


@pytest.mark.parametrize("bits", [0o020, 0o002])
def test_a_group_or_world_writable_dot_git_is_refused(tmp_path, bits):
    sidecar = _fresh(tmp_path)
    mode = (sidecar / ".git").stat().st_mode & 0o777
    (sidecar / ".git").chmod(mode | bits)
    assert hostgit.is_sidecar(sidecar) is False
    assert "writable" in hostgit.sidecar_problem(sidecar)


def test_a_group_writable_sidecar_directory_is_refused(tmp_path):
    sidecar = _fresh(tmp_path)
    sidecar.chmod(0o775)
    assert "writable" in hostgit.sidecar_problem(sidecar)


def test_a_sidecar_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    sidecar = _fresh(tmp_path)
    real_uid = os.getuid()
    monkeypatch.setattr(hostgit.os, "getuid", lambda: real_uid + 1)
    assert "not owned" in hostgit.sidecar_problem(sidecar)


def test_a_missing_sidecar_is_reported_not_raised(tmp_path):
    assert hostgit.sidecar_problem(tmp_path / "nope")
    assert hostgit.is_sidecar(tmp_path / "nope") is False


def test_ambient_warns_once_and_still_runs(tmp_path, caplog):
    sidecar = _fresh(tmp_path)
    sidecar.chmod(0o775)
    with caplog.at_level("WARNING", logger=hostgit.__name__):
        first = hostgit.sidecar(sidecar, "rev-parse", "--git-dir")
        second = hostgit.sidecar(sidecar, "rev-parse", "--git-dir")
    assert first.returncode == 0 and second.returncode == 0  # still ran
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and "writable" in lines[0] and str(sidecar) in lines[0]


def test_a_sealed_guard_refuses_with_rc_125_and_spawns_nothing(
        tmp_path, monkeypatch):
    sidecar = _fresh(tmp_path)
    sidecar.chmod(0o775)
    calls = []
    monkeypatch.setattr(hostgit, "_spawn", lambda *a, **k: calls.append(a))
    refused = hostgit._guard_sidecar(sidecar, sealed=True)
    assert refused.returncode == 125
    assert "writable" in refused.stderr
    assert calls == []
    assert hostgit._guard_sidecar(sidecar, sealed=False) is None


def test_clone_checks_the_directory_it_clones_into(tmp_path, caplog):
    team = tmp_path / "team"
    team.mkdir()
    team.chmod(0o777)
    with caplog.at_level("WARNING", logger=hostgit.__name__):
        proc = hostgit.clone(str(tmp_path / "no-such-remote"), team / "x",
                             timeout=30)
    assert proc.returncode != 0  # nothing to clone; the point is the warning
    assert any("writable" in r.getMessage() for r in caplog.records)
    refused = hostgit._guard_clone(team / "x", sealed=True)
    assert refused.returncode == 125
