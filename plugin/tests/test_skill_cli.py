"""#66: the canonical skill content ships to a host's rules/skills file via
`daimon skill list|show|install|uninstall` — in-process cli.main() calls with
HOME pointed at tmp_path, the pattern test_hooks_install.py uses."""

from daimon_briefing import cli


def test_skill_show_prints_full(capsys):
    assert cli.main(["skill", "show"]) == 0
    out = capsys.readouterr().out
    assert "name: daimon" in out


def test_skill_show_compact(capsys):
    assert cli.main(["skill", "show", "--compact"]) == 0
    assert "MUST" in capsys.readouterr().out


def test_skill_install_claude_global(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["skill", "install", "claude"]) == 0
    assert (tmp_path / ".claude" / "skills" / "daimon" / "SKILL.md").exists()
    assert "installed" in capsys.readouterr().out


def test_skill_install_unknown_host_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cli.main(["skill", "install", "emacs"])
    assert rc == 2
    assert "unknown host" in capsys.readouterr().err


def test_skill_uninstall_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cli.main(["skill", "install", "claude"])
    assert cli.main(["skill", "uninstall", "claude"]) == 0
    assert not (tmp_path / ".claude" / "skills" / "daimon" / "SKILL.md").exists()


def test_skill_list_names_hosts(capsys):
    assert cli.main(["skill", "list"]) == 0
    out = capsys.readouterr().out
    for host in ("claude", "codex", "windsurf", "cursor", "gemini"):
        assert host in out


def test_skill_install_project_writes_relative_to_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["skill", "install", "cursor", "--project"]) == 0
    assert (tmp_path / ".cursor" / "rules" / "daimon.mdc").exists()


def test_skill_install_project_no_git_falls_back_to_cwd(tmp_path, monkeypatch, capsys):
    """--project outside any git repo must still write relative to cwd, not
    crash or silently no-op (config.resolve_project_root falls back to the
    raw path on any git failure — same contract exercised here)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    no_git_dir = tmp_path / "no_git_here"
    no_git_dir.mkdir()
    monkeypatch.chdir(no_git_dir)
    assert cli.main(["skill", "install", "cursor", "--project"]) == 0
    assert (no_git_dir / ".cursor" / "rules" / "daimon.mdc").exists()


# ---- #1012: the rich install summary -----------------------------------------
#
# Same grouping contract as `hooks install`: Installed / Next steps in one
# panel on the rich path; plain keeps the exact pre-#1012 lines.


def test_skill_install_rich_groups_the_summary(tmp_path, monkeypatch, capsys):
    from daimon_briefing import render

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    assert cli.main(["skill", "install", "kimi"]) == 0
    out = capsys.readouterr().out
    assert "daimon skill install kimi" in out
    assert "Installed" in out
    assert "installed daimon skill" in out
    # the upgrade reminder is a next step, not an artifact line
    assert "Next steps" in out
    assert "Re-run `daimon skill install kimi`" in out


def test_skill_install_plain_output_is_unchanged_by_the_summary(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    capsys.readouterr()
    assert cli.main(["skill", "install", "kimi"]) == 0
    out = capsys.readouterr().out
    assert "installed daimon skill" in out
    assert "Installed" not in out and "Next steps" not in out
