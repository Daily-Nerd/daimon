"""#554: `daimon hooks status` audits the hook copies daimon itself installs,
but Claude Code's hooks ship inside the plugin, so its drift was the one host
nothing reported. The CLI and the plugin update through different commands and
neither notices the other. Fixtures build a fake ~/.claude/plugins tree.
"""

import json
from pathlib import Path

from daimon_briefing import cli, render


def _install_plugin(home: Path, version: str, recorded: str | None = None,
                    marketplace: str = "daimon") -> Path:
    """Mimic a marketplace install: a versioned cache dir holding daimon's own
    manifest, plus the host's record of what it installed."""
    root = home / ".claude" / "plugins" / "cache" / marketplace / "daimon" / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "daimon", "version": version}), encoding="utf-8")
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 2, "plugins": {
        f"daimon@{marketplace}": [
            {"scope": "user", "installPath": str(root),
             "version": recorded or version},
        ]}}), encoding="utf-8")
    return root


def test_no_plugin_installed_is_not_drift(tmp_path):
    # A Codex-only or CLI-only install is not broken, it is simply not a plugin
    # user. Nagging it would be noise on every status.
    assert cli._plugin_drift(tmp_path, "0.25.0") is None


def test_matching_versions_are_not_drift(tmp_path):
    _install_plugin(tmp_path, "0.25.0")
    assert cli._plugin_drift(tmp_path, "0.25.0") is None


def test_plugin_behind_the_cli_is_drift(tmp_path):
    # The field case: `uv tool install` moved the CLI, the plugin cache did not.
    _install_plugin(tmp_path, "0.17.0")
    assert cli._plugin_drift(tmp_path, "0.25.0") == {
        "installed": "0.17.0", "cli": "0.25.0", "behind": True,
    }


def test_plugin_ahead_of_the_cli_is_drift_too(tmp_path):
    # Same split, other direction: the plugin updated and the CLI did not. Still
    # a mismatch worth reporting, but the fix is the opposite command.
    _install_plugin(tmp_path, "0.26.0")
    assert cli._plugin_drift(tmp_path, "0.25.0") == {
        "installed": "0.26.0", "cli": "0.25.0", "behind": False,
    }


def test_version_comes_from_daimons_own_manifest_not_the_host_record(tmp_path):
    # Two sources disagree. daimon's manifest ships with the code being run, so
    # it is the one that describes the hooks that will actually execute.
    _install_plugin(tmp_path, "0.17.0", recorded="0.25.0")
    drift = cli._plugin_drift(tmp_path, "0.25.0")
    assert drift is not None and drift["installed"] == "0.17.0"


def test_prerelease_versions_compare_without_a_packaging_dependency():
    # The kernel is stdlib-only, so version compare is hand-rolled. A component
    # with a non-numeric suffix must not crash or sort wrong.
    assert cli._version_tuple("1.0.0rc1") == (1, 0, 0)
    assert cli._version_tuple("0.25.0") < cli._version_tuple("0.26.0")


def test_plugins_key_of_the_wrong_shape_is_not_drift(tmp_path):
    state = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"version": 2, "plugins": []}), encoding="utf-8")
    assert cli._plugin_drift(tmp_path, "0.25.0") is None


def test_non_dict_entries_are_skipped(tmp_path):
    state = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"plugins": {"daimon@daimon": ["junk"]}}),
                     encoding="utf-8")
    assert cli._plugin_drift(tmp_path, "0.25.0") is None


def test_entry_without_an_install_path_falls_back_to_the_recorded_version(tmp_path):
    # No path to read daimon's manifest from, so the host's record is all there
    # is. Better than reporting nothing.
    state = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"plugins": {
        "daimon@daimon": [{"scope": "user", "version": "0.17.0"}]}}),
        encoding="utf-8")
    drift = cli._plugin_drift(tmp_path, "0.25.0")
    assert drift is not None and drift["installed"] == "0.17.0"


def test_corrupt_manifest_falls_back_to_the_recorded_version(tmp_path):
    root = _install_plugin(tmp_path, "0.17.0")
    (root / ".claude-plugin" / "plugin.json").write_text("{not json",
                                                         encoding="utf-8")
    drift = cli._plugin_drift(tmp_path, "0.25.0")
    assert drift is not None and drift["installed"] == "0.17.0"


def test_drift_probe_swallows_an_exploding_home(monkeypatch):
    # Same contract as _hook_drift_present: status never crashes on a probe.
    monkeypatch.setattr(cli.Path, "home", staticmethod(
        lambda: (_ for _ in ()).throw(RuntimeError("no home"))))
    assert cli._plugin_drift_present() is None


# ---- the daimon status pointer -----------------------------------------------


def test_status_names_both_versions_and_the_restart(capsys):
    # Naming both versions is the point: "out of date" alone sent me to the
    # wrong host once already. The restart matters because hooks resolve at
    # session start, so updating the plugin mid-session changes nothing.
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "plugin_drift": {"installed": "0.17.0", "cli": "0.25.0", "behind": True},
    })
    out = capsys.readouterr().out
    assert "0.17.0" in out and "0.25.0" in out
    assert "/plugin" in out
    assert "restart" in out


def test_status_tells_a_plugin_ahead_user_to_upgrade_the_cli(capsys):
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "plugin_drift": {"installed": "0.26.0", "cli": "0.25.0", "behind": False},
    })
    out = capsys.readouterr().out
    assert "uv tool upgrade" in out


def test_rich_status_renders_the_same_warning(capsys, monkeypatch):
    # Both renderers carry the warning or half the users never see it. The rich
    # path is chosen by supports_rich(), so force it rather than depend on
    # whether the pretty extra happens to be installed here.
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "plugin_drift": {"installed": "0.17.0", "cli": "0.25.0", "behind": True},
    })
    out = capsys.readouterr().out
    assert "0.17.0" in out and "0.25.0" in out


def test_status_silent_when_versions_agree(capsys):
    render.render_status({
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": None,
        "plugin_drift": None,
    })
    assert "plugin" not in capsys.readouterr().out


def test_cmd_status_surfaces_a_stale_plugin_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_plugin(tmp_path, "0.0.1")  # any version the running CLI is not
    capsys.readouterr()
    cli.main(["status"])
    assert "0.0.1" in capsys.readouterr().out


def test_unreadable_plugin_state_is_not_drift(tmp_path):
    # status must never crash on someone else's file format.
    state = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json", encoding="utf-8")
    assert cli._plugin_drift(tmp_path, "0.25.0") is None
