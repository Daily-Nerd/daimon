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
    assert text.startswith("---\nname: daimon")
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


def test_codex_global_warns_over_char_cap(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "AGENTS.md").write_text("x" * 32000, encoding="utf-8")
    result_lines = install("codex", project=False, home=home, cwd=tmp_path)
    assert any("32,768" in ln or "32768" in ln for ln in result_lines)


# ---- windsurf global: real skills dir + memories migration (#88) ----


def test_windsurf_global_writes_full_skill(tmp_path):
    # #88 field report: global skills live at
    # ~/.codeium/windsurf/skills/<name>/SKILL.md — memories/global_rules.md is
    # Windsurf's MEMORIES store, not a skills registry.
    _, home, _ = _run("windsurf", tmp_path)
    dest = home / ".codeium" / "windsurf" / "skills" / "daimon" / "SKILL.md"
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("---\nname: daimon")
    assert "daimon brief" in text


def test_windsurf_global_install_migrates_legacy_memories_block(tmp_path):
    # Pre-#88 installs left a marker block in memories/global_rules.md.
    # Reinstall must relocate the skill AND strip the stale block, leaving
    # the user's own memories byte-identical.
    home = tmp_path / "home"
    mem = home / ".codeium" / "windsurf" / "memories"
    mem.mkdir(parents=True)
    user = "# my real memories\n\nprefers tabs.\n"
    legacy = (f"{user}\n<!-- daimon:skill v0.7.0 start -->\nold skill\n"
              "<!-- daimon:skill v0.7.0 end -->\n")
    (mem / "global_rules.md").write_text(legacy, encoding="utf-8")
    install("windsurf", project=False, home=home, cwd=tmp_path)
    assert (home / ".codeium" / "windsurf" / "skills" / "daimon"
            / "SKILL.md").exists()
    assert (mem / "global_rules.md").read_text(encoding="utf-8") == user


def test_windsurf_global_uninstall_cleans_legacy_block_too(tmp_path):
    home = tmp_path / "home"
    mem = home / ".codeium" / "windsurf" / "memories"
    mem.mkdir(parents=True)
    user = "# my real memories\n"
    (mem / "global_rules.md").write_text(
        f"{user}\n<!-- daimon:skill v0.7.0 start -->\nold\n"
        "<!-- daimon:skill v0.7.0 end -->\n", encoding="utf-8")
    install("windsurf", project=False, home=home, cwd=tmp_path)
    uninstall("windsurf", project=False, home=home, cwd=tmp_path)
    assert not (home / ".codeium" / "windsurf" / "skills" / "daimon"
                / "SKILL.md").exists()
    assert (mem / "global_rules.md").read_text(encoding="utf-8") == user


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


# ---- bundled directory-form skills (#1000) ----

def _full_rows():
    from daimon_briefing.skill_install import HOSTS

    for host, scopes in sorted(HOSTS.items()):
        for scope, entry in sorted(scopes.items()):
            if entry is not None and entry[2] == "full":
                yield host, scope, entry[0]


def test_every_full_row_is_directory_form():
    """`full` is not a density label, it is the DIRECTORY-FORM contract:
    a lazily-loaded `<root>/skills/<name>/SKILL.md` the host scans by
    directory. The bundled-skill writer derives its destination root from
    that shape, so a `full` row that stops ending in `skills/daimon/SKILL.md`
    would silently send `daimon-end` somewhere no host reads."""
    rows = list(_full_rows())
    assert rows
    for host, scope, rel in rows:
        assert rel.endswith("skills/daimon/SKILL.md"), f"{host}/{scope}: {rel}"


def test_full_hosts_receive_the_bundled_end_skill(tmp_path):
    """#1000: `daimon-end` was packaged but never installed outside the Claude
    plugin layout, so a kimi (or plain-CLI claude, or windsurf global) user had
    no `/daimon-end` and no way to learn it exists."""
    from daimon_briefing.skill_install import BUNDLED_SKILLS

    for host, scope, rel in _full_rows():
        home = tmp_path / f"{host}-{scope}-home"
        cwd = tmp_path / f"{host}-{scope}-repo"
        home.mkdir()
        cwd.mkdir()
        project = scope == "project"
        install(host, project=project, home=home, cwd=cwd)
        root = (cwd if project else home) / rel
        for name in BUNDLED_SKILLS:
            dest = root.parent.parent / name / "SKILL.md"
            text = dest.read_text(encoding="utf-8")
            # Directory form: the host skips a skill whose frontmatter `name`
            # does not equal its directory name.
            assert text.startswith("---\n"), f"{host}/{scope}/{name}"
            assert f"\nname: {name}\n" in text, f"{host}/{scope}/{name}"
            assert "description:" in text


def test_install_reports_every_file_it_wrote(tmp_path):
    lines, home, _ = _run("kimi", tmp_path)
    joined = "\n".join(lines)
    assert str(home / ".kimi-code" / "skills" / "daimon" / "SKILL.md") in joined
    assert str(home / ".kimi-code" / "skills" / "daimon-end" / "SKILL.md") in joined


def test_compact_hosts_get_no_bundled_skill(tmp_path):
    """The rules-file hosts concatenate one file into every prompt; they have
    no directory to scan, so a second skill there is content nobody loads."""
    _, _home, cwd = _run("cursor", tmp_path, project=True)
    assert not list(cwd.rglob("daimon-end"))


def test_uninstall_removes_the_bundled_skill(tmp_path):
    _, home, _ = _run("kimi", tmp_path)
    skills = home / ".kimi-code" / "skills"
    assert (skills / "daimon-end" / "SKILL.md").exists()
    uninstall("kimi", project=False, home=home, cwd=home)
    assert not (skills / "daimon" / "SKILL.md").exists()
    assert not (skills / "daimon-end" / "SKILL.md").exists()
    # Owned-file semantics: daimon created the directory, daimon takes it back,
    # but only while it holds nothing else.
    assert not (skills / "daimon-end").exists()


def test_uninstall_keeps_a_bundled_directory_holding_user_files(tmp_path):
    _, home, _ = _run("kimi", tmp_path)
    stray = home / ".kimi-code" / "skills" / "daimon-end" / "notes.md"
    stray.write_text("mine\n", encoding="utf-8")
    uninstall("kimi", project=False, home=home, cwd=home)
    assert stray.read_text(encoding="utf-8") == "mine\n"


def test_uninstall_without_an_install_does_not_raise(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    lines = uninstall("kimi", project=False, home=home, cwd=home)
    assert any("nothing installed" in line for line in lines)


def test_a_build_missing_its_packaged_skill_refuses_loudly(tmp_path, monkeypatch):
    """Package data absent is a BUILD fault, not a user condition. Writing the
    `daimon` skill and silently skipping the rest would reproduce #1000 with
    the installer reporting success."""
    from daimon_briefing import skill_install

    monkeypatch.setattr(skill_install, "_BUNDLED_DIR", tmp_path / "gone")
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(SkillInstallError) as exc:
        install("kimi", project=False, home=home, cwd=home)
    assert "daimon-end" in str(exc.value)
