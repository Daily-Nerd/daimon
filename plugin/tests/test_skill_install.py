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
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    return install(host, project=project, home=home, cwd=cwd), home, cwd


# ---- owned-file family ----

def test_claude_global_writes_full_skill(tmp_path):
    _, home, _ = _run("claude", tmp_path)
    dest = home / ".claude" / "skills" / "daimon" / "SKILL.md"
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("---\nname: daimon")
    assert "daimon brief" in text


def test_cursor_global_is_skills_only_and_says_so(tmp_path):
    """Cursor global rules live in IDE settings, so there is no file to write.
    The row is a skills-only record, not a missing one (#1327)."""
    lines, home, _ = _run("cursor", tmp_path)
    skill = home / ".cursor" / "skills" / "daimon-end" / "SKILL.md"
    assert skill.exists()
    assert not (home / ".cursor" / "rules").exists()
    assert sorted(p.relative_to(home).as_posix()
                  for p in home.rglob("*") if p.is_file()) == [
        ".cursor/skills/daimon-end/SKILL.md"]
    out = "\n".join(lines)
    assert "skills only" in out and "IDE settings" in out
    assert str(skill) in out


def test_cursor_global_uninstall_removes_only_the_skill_folder(tmp_path):
    _, home, cwd = _run("cursor", tmp_path)
    skills = home / ".cursor" / "skills"
    (skills / "other").mkdir()
    (skills / "other" / "SKILL.md").write_text("theirs\n", encoding="utf-8")
    stray = skills / "daimon-end" / "notes.md"
    stray.write_text("mine\n", encoding="utf-8")
    uninstall("cursor", project=False, home=home, cwd=cwd)
    assert stray.read_text(encoding="utf-8") == "mine\n"
    assert not (skills / "daimon-end" / "SKILL.md").exists()
    assert (skills / "other" / "SKILL.md").read_text(encoding="utf-8") == "theirs\n"


def test_cursor_global_uninstall_without_install_is_quiet(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    lines = uninstall("cursor", project=False, home=home, cwd=home)
    assert any("nothing installed" in ln for ln in lines)
    assert not (home / ".cursor").exists()


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


def test_uninstall_cleans_a_legacy_block_when_nothing_was_installed(tmp_path):
    """The pre-#88 block outlives the install that would have stripped it: a
    person who upgraded and never re-installed still has skill content sitting
    in their MEMORIES file, and `uninstall` is the verb they reach for. It has
    to clean that even though it finds no skill of its own to remove.

    The existing migrate/uninstall pair never reached this branch, because
    installing first strips the block, so uninstall then saw a clean file."""
    home = tmp_path / "home"
    mem = home / ".codeium" / "windsurf" / "memories"
    mem.mkdir(parents=True)
    user = "# my real memories\n"
    (mem / "global_rules.md").write_text(
        f"{user}\n<!-- daimon:skill v0.7.0 start -->\nold\n"
        "<!-- daimon:skill v0.7.0 end -->\n", encoding="utf-8")
    lines = uninstall("windsurf", project=False, home=home, cwd=tmp_path)
    assert (mem / "global_rules.md").read_text(encoding="utf-8") == user
    assert any("legacy daimon:skill block" in line for line in lines)
    assert any("nothing installed" in line for line in lines)


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

def _rows():
    """Every (host, scope, spec) with a delivery row."""
    from daimon_briefing.skill_install import HOSTS

    for host, scopes in sorted(HOSTS.items()):
        for scope, spec in sorted(scopes.items()):
            if spec is not None:
                yield host, scope, spec


def _skill_rows():
    return [(h, s, spec) for h, s, spec in _rows() if spec.skills_root]


def test_every_row_has_a_skills_root_or_an_explicit_unsupported_reason():
    """#1327: a host that cannot take the bundled skills says so, with a
    reason. Silence is how Codex, Gemini and Cursor went without one."""
    from daimon_briefing.skill_install import HOSTS, UNSUPPORTED_SKILLS

    for host, scopes in sorted(HOSTS.items()):
        for scope, spec in sorted(scopes.items()):
            if spec is not None and spec.skills_root:
                assert (host, scope) not in UNSUPPORTED_SKILLS
                continue
            assert UNSUPPORTED_SKILLS.get((host, scope)), f"{host}/{scope}"
    assert UNSUPPORTED_SKILLS[("windsurf", "project")]
    assert ("cursor", "global") not in UNSUPPORTED_SKILLS


def test_unsupported_rows_name_only_real_rows():
    from daimon_briefing.skill_install import HOSTS, UNSUPPORTED_SKILLS

    for host, scope in UNSUPPORTED_SKILLS:
        assert scope in HOSTS[host], f"{host}/{scope}"


def test_every_skills_root_is_a_relative_path_without_traversal():
    for host, scope, spec in _skill_rows():
        assert not spec.skills_root.startswith("/"), f"{host}/{scope}"
        assert ".." not in spec.skills_root.split("/"), f"{host}/{scope}"


def test_no_two_hosts_share_a_skills_folder():
    """Skill folder is the unit of ownership. Two hosts resolving to one
    folder would let one host's uninstall delete the other's skill, so the
    shared case needs an explicit user count before it is allowed."""
    seen: dict[tuple[str, str], str] = {}
    for host, scope, spec in _skill_rows():
        key = (scope, spec.skills_root)
        assert key not in seen, f"{host}/{scope} shares {key} with {seen[key]}"
        seen[key] = host


def test_every_directory_form_row_still_ends_in_its_skills_root():
    """The `daimon` skill of a directory-form host lives inside the root the
    bundled skills use, so the two can never diverge."""
    for host, scope, spec in _skill_rows():
        if spec.density == "full":
            assert spec.rel == f"{spec.skills_root}/daimon/SKILL.md", f"{host}/{scope}"


def test_every_host_with_a_skills_root_receives_every_bundled_skill(tmp_path):
    """#1327 (and #1000): table-driven over every row that has a skills_root,
    whatever its density."""
    from daimon_briefing.skill_install import BUNDLED_SKILLS

    rows = _skill_rows()
    assert {h for h, _, _ in rows} >= {"claude", "codex", "cursor", "gemini",
                                       "kimi", "windsurf"}
    for host, scope, spec in rows:
        home = tmp_path / f"{host}-{scope}-home"
        cwd = tmp_path / f"{host}-{scope}-repo"
        home.mkdir()
        cwd.mkdir()
        project = scope == "project"
        install(host, project=project, home=home, cwd=cwd)
        base = cwd if project else home
        for name in BUNDLED_SKILLS:
            text = (base / spec.skills_root / name / "SKILL.md").read_text(
                encoding="utf-8")
            # Directory form: the host skips a skill whose frontmatter `name`
            # does not equal its directory name.
            assert text.startswith("---\n"), f"{host}/{scope}/{name}"
            assert f"\nname: {name}\n" in text, f"{host}/{scope}/{name}"
            assert "description:" in text


def test_density_never_decides_which_skills_exist(tmp_path, monkeypatch):
    """The root cause of #1327, pinned directly: the same host with compact
    and with full density writes identical bundled output."""
    import dataclasses

    from daimon_briefing import skill_install

    out = {}
    for density in ("compact", "full"):
        spec = skill_install.HOSTS["codex"]["global"]
        row = spec._replace(density=density) if hasattr(spec, "_replace") \
            else dataclasses.replace(spec, density=density)
        monkeypatch.setitem(skill_install.HOSTS["codex"], "global", row)
        home = tmp_path / density
        home.mkdir()
        install("codex", project=False, home=home, cwd=home)
        out[density] = (home / spec.skills_root / "daimon-end" / "SKILL.md"
                        ).read_text(encoding="utf-8")
    assert out["compact"] == out["full"]


def test_codex_skills_land_in_the_documented_agents_directory(tmp_path):
    _, home, _ = _run("codex", tmp_path)
    assert (home / ".agents" / "skills" / "daimon-end" / "SKILL.md").exists()
    _, home2, cwd2 = _run("codex", tmp_path / "p", project=True)
    assert (cwd2 / ".agents" / "skills" / "daimon-end" / "SKILL.md").exists()


def test_codex_block_stays_compact_with_a_realistic_agents_md(tmp_path):
    from daimon_briefing.skill_install import CHAR_CAPS

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    user = "# My rules\n" + "- keep functions small\n" * 400
    (home / ".codex" / "AGENTS.md").write_text(user, encoding="utf-8")
    lines = install("codex", project=False, home=home, cwd=tmp_path)
    text = (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= CHAR_CAPS["codex"]
    assert not any(ln.startswith("warning") for ln in lines)
    # The block carries the protocol only, never the daimon-end body.
    assert "In-session self-serialization" not in text


def test_install_names_the_invocation_each_host_actually_has(tmp_path):
    """Codex invokes by `$name` or `/skills`; Gemini activates by description
    and has no by-name invocation. Promising `/daimon-end` there is false."""
    def joined(host):
        lines, _h, _c = _run(host, tmp_path / host)
        # Only the wording after the written path: the path itself contains
        # the directory name `daimon-end`.
        return "\n".join(ln.split("SKILL.md", 1)[1] for ln in lines
                         if "skill (daimon-end)" in ln)

    codex = joined("codex")
    assert "$daimon-end" in codex and "/daimon-end" not in codex
    gemini = joined("gemini")
    assert "/daimon-end" not in gemini and "$daimon-end" not in gemini
    assert "description" in gemini
    assert "/daimon-end" in joined("kimi")


def test_gemini_and_cursor_are_wired_both_scopes_they_support(tmp_path):
    _, _, cwd = _run("cursor", tmp_path / "c", project=True)
    assert (cwd / ".cursor" / "skills" / "daimon-end" / "SKILL.md").exists()
    _, home, _ = _run("gemini", tmp_path / "g")
    assert (home / ".gemini" / "skills" / "daimon-end" / "SKILL.md").exists()
    _, _, cwd2 = _run("gemini", tmp_path / "g2", project=True)
    assert (cwd2 / ".gemini" / "skills" / "daimon-end" / "SKILL.md").exists()


def test_install_reports_every_file_it_wrote(tmp_path):
    lines, home, _ = _run("kimi", tmp_path)
    joined = "\n".join(lines)
    assert str(home / ".kimi-code" / "skills" / "daimon" / "SKILL.md") in joined
    assert str(home / ".kimi-code" / "skills" / "daimon-end" / "SKILL.md") in joined


def test_only_the_explicit_unsupported_rows_get_no_bundled_skill(tmp_path):
    """Rewritten from the pin that froze #1327 as intended behavior: the one
    install path that still writes no `daimon-end` is a row the table names
    as unsupported, with its reason."""
    from daimon_briefing.skill_install import BUNDLED_SKILLS, UNSUPPORTED_SKILLS

    assert ("windsurf", "project") in UNSUPPORTED_SKILLS
    _, _home, cwd = _run("windsurf", tmp_path, project=True)
    for name in BUNDLED_SKILLS:
        assert not list(cwd.rglob(name))


def test_uninstall_codex_removes_the_skill_and_only_the_skill(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    user = "# Mine\n"
    (home / ".codex" / "AGENTS.md").write_bytes(user.encode())
    install("codex", project=False, home=home, cwd=tmp_path)
    shared = home / ".agents" / "skills"
    (shared / "other-skill").mkdir()
    (shared / "other-skill" / "SKILL.md").write_text("theirs\n", encoding="utf-8")
    stray = shared / "notes.md"
    stray.write_text("mine\n", encoding="utf-8")
    lines = uninstall("codex", project=False, home=home, cwd=tmp_path)
    assert not (shared / "daimon-end").exists()
    assert (shared / "other-skill" / "SKILL.md").read_text(encoding="utf-8") == "theirs\n"
    assert stray.read_text(encoding="utf-8") == "mine\n"
    assert (home / ".codex" / "AGENTS.md").read_bytes() == user.encode()
    assert any("daimon-end" in ln for ln in lines)


def test_uninstall_still_removes_the_skill_when_the_block_is_already_gone(tmp_path):
    """The skill and the block are separate artifacts: a user who deleted the
    block by hand must still be able to take the skill back, and be told."""
    home = tmp_path / "home"
    install("codex", project=False, home=home, cwd=tmp_path)
    (home / ".codex" / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    lines = uninstall("codex", project=False, home=home, cwd=tmp_path)
    assert not (home / ".agents" / "skills" / "daimon-end").exists()
    assert any("daimon-end" in ln for ln in lines)


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
