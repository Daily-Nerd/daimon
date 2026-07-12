"""#43: hook scripts ship in the package; `daimon hooks install <host>` puts
them at a stable path so registration survives every upgrade."""

import json
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

_WINDSURF_FILES = cli._HOOK_HOSTS["windsurf"]["files"]
_CODEX_SCRIPTS = ("daimon-codex-session-start.py", "daimon-codex-stop.py")
_CODEX_FILES = _CODEX_SCRIPTS + ("_daimon_hook_lib.py",)


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
    for name in _WINDSURF_FILES:
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


# ---- codex: two scripts, two events, real hooks.json registration (#262) ----


def test_codex_scripts_are_packaged_and_drift_guarded():
    # The drift guard (test_packaged_hook_matches_repo_copy) parametrizes over
    # _SHIPPED, so proving the codex scripts are in _SHIPPED proves they are
    # both packaged AND covered by the byte-identity drift test.
    for name in _CODEX_SCRIPTS:
        assert name in _SHIPPED, f"{name} not shipped via sync_hooks manifest"


def test_hooks_list_names_codex_with_events(capsys):
    assert cli.main(["hooks", "list"]) == 0
    out = capsys.readouterr().out
    assert "codex" in out
    assert "SessionStart" in out and "Stop" in out


def test_hooks_install_codex_copies_scripts_and_lib_to_codex_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    hooks_dir = tmp_path / ".codex" / "hooks"
    for name in _CODEX_FILES:
        p = hooks_dir / name
        assert p.is_file(), f"{name} not installed"
        assert p.read_bytes() == (PKG_HOOKS_DIR / name).read_bytes()
    # Codex executes the scripts directly, so they must be executable.
    for name in _CODEX_SCRIPTS:
        assert (hooks_dir / name).stat().st_mode & stat.S_IXUSR, f"{name} not executable"


def test_hooks_install_codex_registers_both_events(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    cfg = json.loads((tmp_path / ".codex" / "hooks.json").read_text())["hooks"]

    assert "SessionStart" in cfg and "Stop" in cfg
    ss = cfg["SessionStart"][0]
    assert ss["matcher"] == "startup|resume"
    ss_hook = ss["hooks"][0]
    assert "daimon-codex-session-start.py" in ss_hook["command"]
    assert ss_hook["timeout"] == 10
    assert ss_hook["statusMessage"] == "Reading daimon briefing..."

    stop_hook = cfg["Stop"][0]["hooks"][0]
    assert "daimon-codex-stop.py" in stop_hook["command"]
    assert stop_hook["statusMessage"] == "Writing daimon checkpoint..."


def test_hooks_install_codex_preserves_unrelated_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex = tmp_path / ".codex"
    codex.mkdir()
    (codex / "hooks.json").write_text(json.dumps({
        "hooks": {
            # a foreign entry under an event daimon ALSO uses
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "python3 /other/thing.py"}]}
            ],
            # an event daimon never touches
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "echo hi"}]}
            ],
        }
    }))
    assert cli.main(["hooks", "install", "codex"]) == 0
    cfg = json.loads((codex / "hooks.json").read_text())["hooks"]

    ss_cmds = [h["command"] for g in cfg["SessionStart"] for h in g["hooks"]]
    assert "python3 /other/thing.py" in ss_cmds  # foreign entry untouched
    assert any("daimon-codex-session-start.py" in c for c in ss_cmds)  # ours added
    assert cfg["PreToolUse"][0]["hooks"][0]["command"] == "echo hi"  # unrelated event kept


def test_hooks_install_codex_recovers_corrupt_hooks_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    codex = tmp_path / ".codex"
    codex.mkdir()
    (codex / "hooks.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["hooks", "install", "codex"]) == 0
    cfg = json.loads((codex / "hooks.json").read_text())["hooks"]
    assert any("daimon-codex-session-start.py" in h["command"]
               for g in cfg["SessionStart"] for h in g["hooks"])
    # the corrupt original is preserved as a backup, not silently destroyed
    backups = list(codex.glob("hooks.json.daimon-backup-*"))
    assert backups and backups[0].read_text(encoding="utf-8") == "{not json"


def test_hooks_install_codex_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert cli.main(["hooks", "install", "codex"]) == 0  # re-run must not duplicate
    cfg = json.loads((tmp_path / ".codex" / "hooks.json").read_text())["hooks"]
    assert len(cfg["SessionStart"]) == 1
    assert len(cfg["Stop"]) == 1


def test_hooks_install_codex_refreshes_stale_script(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    stale = tmp_path / ".codex" / "hooks" / "daimon-codex-stop.py"
    stale.write_text("# stale old version")
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert stale.read_bytes() == (PKG_HOOKS_DIR / "daimon-codex-stop.py").read_bytes()


def test_hooks_install_codex_output_tells_user_to_trust(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    out = capsys.readouterr().out
    assert "/hooks" in out
    assert "trust" in out.lower()


def test_hooks_install_codex_is_lifecycle_not_skill(tmp_path, monkeypatch):
    # `daimon hooks install codex` installs LIFECYCLE hooks (scripts + hooks.json).
    # It must NOT write the agent skill — ~/.codex/AGENTS.md is the skill's file.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


def test_skill_install_codex_is_not_lifecycle_hooks(tmp_path, monkeypatch):
    # Converse: `daimon skill install codex` teaches the agent (AGENTS.md) and
    # must NOT register lifecycle hooks — no hooks.json.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["skill", "install", "codex"]) == 0
    assert (tmp_path / ".codex" / "AGENTS.md").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
