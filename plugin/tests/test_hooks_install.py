"""#43: hook scripts ship in the package; `daimon hooks install <host>` puts
them at a stable path so registration survives every upgrade."""

import stat
import sys
from pathlib import Path

import pytest

from daimon_briefing import cli

REPO_HOOK_DIR = Path(__file__).parents[2] / "hook"
PKG_HOOKS_DIR = Path(__file__).parents[1] / "daimon_briefing" / "_hooks"

_SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from sync_hooks import SYNC_PAIRS  # noqa: E402

# Derived from the one shared manifest — the names copied into the packaged
# _hooks/ dir — so this list can never drift from what the sync script ships.
_PKG_HOOKS_REL = "plugin/daimon_briefing/_hooks/"
_SHIPPED = tuple(
    Path(dst).name for _, dst in SYNC_PAIRS if dst.startswith(_PKG_HOOKS_REL)
)


@pytest.mark.parametrize("name", _SHIPPED)
def test_packaged_hook_matches_repo_copy(name):
    # Drift guard: the packaged copy IS the repo script, byte for byte. If
    # you edited hook/<name>, copy it into daimon_briefing/_hooks/ too.
    repo = (REPO_HOOK_DIR / name).read_bytes()
    packaged = (PKG_HOOKS_DIR / name).read_bytes()
    assert repo == packaged, f"{name}: repo hook/ and packaged _hooks/ differ"


def test_shipped_redact_matches_canonical_module():
    # #109: the standalone hooks scrub secrets with a redact.py shipped next to
    # them (they cannot import the venv-only package). It MUST stay byte-
    # identical to the canonical module, so patterns — and scar 0022's long-
    # input backtracking guarantee — live in ONE place and never drift.
    canonical = (Path(__file__).parents[1] / "daimon_briefing" / "redact.py").read_bytes()
    shipped = (PKG_HOOKS_DIR / "redact.py").read_bytes()
    assert shipped == canonical, "hook-shipped redact.py drifted from the canonical module"


def test_hooks_install_windsurf_writes_stable_executable_copies(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cli.main(["hooks", "install", "windsurf"])
    assert rc == 0
    target = tmp_path / ".daimon" / "hooks"
    for name in _SHIPPED:
        p = target / name
        assert p.is_file(), f"{name} not installed"
        assert p.stat().st_mode & stat.S_IXUSR, f"{name} not executable"
        assert p.read_bytes() == (PKG_HOOKS_DIR / name).read_bytes()
    out = capsys.readouterr().out
    # the registration snippet points at the STABLE installed path
    assert str(target / "daimon-windsurf-hooks.py") in out
    assert "pre_user_prompt" in out and "post_cascade_response" in out


def test_hooks_install_is_idempotent_refresh(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    stale = tmp_path / ".daimon" / "hooks" / "daimon-windsurf-hooks.py"
    stale.write_text("# stale old version")
    assert cli.main(["hooks", "install", "windsurf"]) == 0
    assert stale.read_bytes() == (PKG_HOOKS_DIR / "daimon-windsurf-hooks.py").read_bytes()


def test_hooks_install_unknown_host_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cli.main(["hooks", "install", "emacs"])
    assert rc == 2
    assert "emacs" in capsys.readouterr().err


def test_hooks_list_names_windsurf(capsys):
    rc = cli.main(["hooks", "list"])
    assert rc == 0
    assert "windsurf" in capsys.readouterr().out
