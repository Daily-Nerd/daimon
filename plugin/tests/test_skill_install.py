"""Writer-family tests for daimon skill install (#66).

The marker-block writer touches files daimon does NOT own (AGENTS.md,
GEMINI.md, Windsurf global rules) — user content outside the markers must
survive byte-identical, and half-broken marker state must refuse rather
than guess.
"""

import pytest

from daimon_briefing.skill_install import SkillInstallError, install, uninstall


def _run(host, tmp_path, project=False):
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    home.mkdir(exist_ok=True)
    cwd.mkdir(exist_ok=True)
    return install(host, project=project, home=home, cwd=cwd), home, cwd


# ---- owned-file family ----

def test_claude_global_writes_full_skill(tmp_path):
    _, home, _ = _run("claude", tmp_path)
    dest = home / ".claude" / "skills" / "daimon" / "SKILL.md"
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("---\nname: using-daimon-memory")
    assert "daimon brief" in text


def test_cursor_is_project_only(tmp_path):
    with pytest.raises(SkillInstallError, match="project"):
        _run("cursor", tmp_path)


def test_cursor_project_writes_mdc(tmp_path):
    _, _, cwd = _run("cursor", tmp_path, project=True)
    text = (cwd / ".cursor" / "rules" / "daimon.mdc").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "alwaysApply: true" in text


def test_windsurf_project_writes_rule_file(tmp_path):
    _, _, cwd = _run("windsurf", tmp_path, project=True)
    text = (cwd / ".windsurf" / "rules" / "daimon.md").read_text(encoding="utf-8")
    assert "trigger: always_on" in text


# ---- marker-block family ----

def test_codex_global_creates_agents_md_with_markers(tmp_path):
    _, home, _ = _run("codex", tmp_path)
    text = (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("<!-- daimon:skill") == 2
    assert "daimon brief" in text


def test_block_appends_to_existing_file_preserving_user_content(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    user = "# My own rules\n\nAlways use tabs.\n"
    (home / ".codex" / "AGENTS.md").write_text(user, encoding="utf-8")
    install("codex", project=False, home=home, cwd=tmp_path)
    text = (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(user)          # user content byte-identical, block APPENDED (end = winning position)
    assert text.count("<!-- daimon:skill") == 2


def test_block_reinstall_is_idempotent(tmp_path):
    _, home, cwd = _run("gemini", tmp_path)
    first = (home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    install("gemini", project=False, home=home, cwd=cwd)
    assert (home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8") == first


def test_block_replaces_stale_version(tmp_path):
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    stale = ("before\n<!-- daimon:skill v0.0.1 start -->\nold\n"
             "<!-- daimon:skill v0.0.1 end -->\nafter\n")
    (home / ".gemini" / "GEMINI.md").write_text(stale, encoding="utf-8")
    install("gemini", project=False, home=home, cwd=tmp_path)
    text = (home / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
    # "old" alone is a substring of real body prose ("older session") —
    # pin the actual intent: the stale version-stamped block is gone.
    assert "v0.0.1" not in text
    assert "<!-- daimon:skill v0.0.1 start -->\nold\n" not in text
    assert text.startswith("before\n")
    assert "\nafter\n" in text
    assert text.count("<!-- daimon:skill") == 2


def test_half_broken_markers_refuse(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "AGENTS.md").write_text(
        "x\n<!-- daimon:skill v0.1 start -->\norphan\n", encoding="utf-8")
    with pytest.raises(SkillInstallError, match="marker"):
        install("codex", project=False, home=home, cwd=tmp_path)


def test_windsurf_global_warns_over_char_cap(tmp_path):
    home = tmp_path / "home"
    rules = home / ".codeium" / "windsurf" / "memories"
    rules.mkdir(parents=True)
    (rules / "global_rules.md").write_text("x" * 5000, encoding="utf-8")
    result_lines = install("windsurf", project=False, home=home, cwd=tmp_path)
    assert any("6,000" in ln or "6000" in ln for ln in result_lines)


# ---- uninstall ----

def test_uninstall_owned_removes_file(tmp_path):
    _, home, cwd = _run("claude", tmp_path)
    uninstall("claude", project=False, home=home, cwd=cwd)
    assert not (home / ".claude" / "skills" / "daimon" / "SKILL.md").exists()


def test_uninstall_block_removes_only_block(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    user = "# Mine\n"
    (home / ".codex" / "AGENTS.md").write_text(user, encoding="utf-8")
    install("codex", project=False, home=home, cwd=tmp_path)
    uninstall("codex", project=False, home=home, cwd=tmp_path)
    assert (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8") == user


def test_unknown_host_refuses(tmp_path):
    with pytest.raises(SkillInstallError, match="unknown host"):
        _run("emacs", tmp_path)
