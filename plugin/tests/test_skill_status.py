"""Skill drift audit (#1006).

The hooks half of this was closed by #266: `daimon hooks status` byte-hashes
what is installed against what is packaged. The skill half had nothing, and
the artifact written for the three directory-form hosts carried no version
marker either, so a stale skill was invisible in both directions.

A stale skill fails the way stale instructions always fail: silently, and in
the direction of the agent doing an older thing correctly.
"""

import re

import pytest

from daimon_briefing import __version__, skill_install


def _home(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return home


def _rows(report, host, skill="daimon", scope="global"):
    return next(r for r in report
                if r["host"] == host and r["skill"] == skill
                and r["scope"] == scope)


# ---- the stamp ----

def test_the_full_variant_carries_the_version_it_was_written_at(tmp_path):
    """`lambda body: body` was the whole wrapper for `full`, so the file
    written for claude, windsurf global and kimi said nothing about which
    release wrote it. The wrapped variants have carried a stamp since #66."""
    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    text = (home / ".claude" / "skills" / "daimon" / "SKILL.md").read_text(
        encoding="utf-8")
    assert f"<!-- daimon:skill v{__version__} -->" in text


def test_the_stamp_never_disturbs_the_frontmatter_contract(tmp_path):
    """Directory-form hosts require `name` and `description` in frontmatter,
    and Kimi skips a skill whose `name` does not equal its directory. A stamp
    that lands inside or before that block silently disables the skill."""
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    text = (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").read_text(
        encoding="utf-8")
    assert text.startswith("---\nname: daimon\n")
    front, _, body = text.partition("\n---\n")
    assert "daimon:skill" not in front
    assert "daimon:skill" in body.split("\n\n", 1)[0]


def test_the_stamp_is_not_repeated_on_reinstall(tmp_path):
    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    text = (home / ".claude" / "skills" / "daimon" / "SKILL.md").read_text(
        encoding="utf-8")
    assert len(re.findall(r"<!-- daimon:skill v\S+ -->", text)) == 1


# ---- the audit ----

def test_nothing_installed_reports_not_installed(tmp_path):
    report = skill_install.audit(home=_home(tmp_path), cwd=tmp_path)
    assert report
    assert all(r["state"] == "NOT INSTALLED" for r in report)
    assert all(r["drift"] is False for r in report), (
        "a host nobody installed has not drifted; it is simply absent")


def test_a_fresh_install_is_current(tmp_path):
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    report = skill_install.audit(home=home, cwd=tmp_path)
    row = _rows(report, "kimi")
    assert row["state"] == "CURRENT"
    assert row["version"] == __version__
    assert row["drift"] is False


def test_an_edited_skill_is_stale(tmp_path):
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    dest = home / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    dest.write_text(dest.read_text(encoding="utf-8").replace(
        "daimon brief", "daimon briefing"), encoding="utf-8")
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi")
    assert row["state"] == "STALE"
    assert row["drift"] is True


def test_a_version_bump_alone_is_not_drift(tmp_path):
    """The stamp records which release wrote the file. Comparing it byte for
    byte would call every release a drift even when the protocol text did not
    move, and an audit that cries wolf on every upgrade stops being read."""
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    dest = home / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    dest.write_text(re.sub(r"<!-- daimon:skill v\S+ -->",
                           "<!-- daimon:skill v0.1.0 -->",
                           dest.read_text(encoding="utf-8")), encoding="utf-8")
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi")
    assert row["state"] == "CURRENT"
    assert row["version"] == "0.1.0", "the row still reports what wrote the file"
    assert row["drift"] is False


def test_a_stale_bundled_skill_drifts_too(tmp_path):
    """#1000 added a second installed skill. It is a packaged file copy, so it
    goes stale exactly the way the generated one does."""
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    dest = home / ".kimi-code" / "skills" / "daimon-end" / "SKILL.md"
    dest.write_text("stale\n", encoding="utf-8")
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi",
                skill="daimon-end")
    assert row["state"] == "STALE"
    assert row["drift"] is True


def test_a_bundled_skill_deleted_by_hand_is_missing_not_absent(tmp_path):
    """The host is installed and one of its skills is gone. That is a
    different repair from "never installed here", so it gets its own word."""
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    (home / ".kimi-code" / "skills" / "daimon-end" / "SKILL.md").unlink()
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi",
                skill="daimon-end")
    assert row["state"] == "MISSING"
    assert row["drift"] is True


def test_compact_hosts_are_audited_without_their_bundled_row(tmp_path):
    home = _home(tmp_path)
    skill_install.install("cursor", project=True, home=home, cwd=tmp_path)
    report = skill_install.audit(home=home, cwd=tmp_path)
    assert _rows(report, "cursor", scope="project")["state"] == "CURRENT"
    assert not [r for r in report
                if r["host"] == "cursor" and r["skill"] == "daimon-end"]


# ---- marker-block hosts ----

def test_a_block_host_compares_only_its_own_region(tmp_path):
    """codex and gemini write into a file the USER owns. Their own rules
    changing is not daimon drift, and reporting it as such would train the
    reader to ignore the audit."""
    home = _home(tmp_path)
    skill_install.install("codex", project=False, home=home, cwd=tmp_path)
    dest = home / ".codex" / "AGENTS.md"
    dest.write_text("# my own rules, rewritten\n\n"
                    + dest.read_text(encoding="utf-8"), encoding="utf-8")
    assert _rows(skill_install.audit(home=home, cwd=tmp_path),
                 "codex")["state"] == "CURRENT"


def test_a_block_host_with_an_edited_region_is_stale(tmp_path):
    home = _home(tmp_path)
    skill_install.install("codex", project=False, home=home, cwd=tmp_path)
    dest = home / ".codex" / "AGENTS.md"
    dest.write_text(dest.read_text(encoding="utf-8").replace(
        "daimon brief", "daimon briefing"), encoding="utf-8")
    assert _rows(skill_install.audit(home=home, cwd=tmp_path),
                 "codex")["state"] == "STALE"


def test_a_file_with_no_daimon_block_is_not_installed(tmp_path):
    home = _home(tmp_path)
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "AGENTS.md").write_text("# mine only\n", encoding="utf-8")
    assert _rows(skill_install.audit(home=home, cwd=tmp_path),
                 "codex")["state"] == "NOT INSTALLED"


def test_half_broken_markers_are_reported_not_raised(tmp_path):
    """`install` refuses on this state rather than guessing at the boundary.
    An AUDIT must never raise: it is the command a person runs to find out
    what is wrong."""
    home = _home(tmp_path)
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "AGENTS.md").write_text(
        "# mine\n\n<!-- daimon:skill v0.1.0 start -->\norphan\n", encoding="utf-8")
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "codex")
    assert row["state"] == "BROKEN"
    assert row["drift"] is True


def test_an_unreadable_destination_is_reported_not_raised(tmp_path, monkeypatch):
    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)

    def boom(self, *a, **kw):
        raise OSError("nope")

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi")
    assert row["state"] == "UNREADABLE"
    assert row["drift"] is True


# ---- scopes ----

def test_both_scopes_are_reported_for_every_host_that_has_them(tmp_path):
    report = skill_install.audit(home=_home(tmp_path), cwd=tmp_path)
    seen = {(r["host"], r["scope"]) for r in report}
    for host, spec in skill_install.HOSTS.items():
        for scope in ("global", "project"):
            assert ((host, scope) in seen) == (spec.get(scope) is not None), (
                f"{host}/{scope}")


def test_project_rows_resolve_against_the_given_cwd(tmp_path):
    home = _home(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    skill_install.install("cursor", project=True, home=home, cwd=repo)
    report = skill_install.audit(home=home, cwd=repo)
    assert _rows(report, "cursor", scope="project")["state"] == "CURRENT"
    other = tmp_path / "elsewhere"
    other.mkdir()
    report = skill_install.audit(home=home, cwd=other)
    assert _rows(report, "cursor", scope="project")["state"] == "NOT INSTALLED"


# ---- the CLI ----

@pytest.fixture
def cli_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PATH", "")
    return h


def test_skill_status_exits_zero_when_nothing_drifted(cli_home, capsys):
    import daimon_briefing.cli as cli

    assert cli.main(["skill", "install", "kimi"]) == 0
    capsys.readouterr()
    assert cli.main(["skill", "status"]) == 0
    assert "kimi" in capsys.readouterr().out


def test_skill_status_exits_non_zero_on_drift(cli_home, capsys):
    import daimon_briefing.cli as cli

    cli.main(["skill", "install", "kimi"])
    dest = cli_home / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    dest.write_text("drifted\n", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["skill", "status"]) == 1
    out = capsys.readouterr().out
    assert "STALE" in out
    assert "daimon skill install" in out, "an audit names its own repair"


def test_skill_status_json_is_machine_readable(cli_home, capsys):
    import json

    import daimon_briefing.cli as cli

    cli.main(["skill", "install", "kimi"])
    capsys.readouterr()
    cli.main(["skill", "status", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert {"host", "scope", "skill", "path", "state", "version", "drift"} <= set(
        rows[0])


def test_daimon_status_points_at_skill_drift(cli_home, capsys):
    """One command still answers "is this machine current". Hook drift already
    surfaces there; skill drift went unmentioned."""
    import daimon_briefing.cli as cli

    cli.main(["skill", "install", "kimi"])
    dest = cli_home / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    dest.write_text("drifted\n", encoding="utf-8")
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "daimon skill status" in out


def test_daimon_status_stays_quiet_when_the_skill_is_current(cli_home, capsys):
    import daimon_briefing.cli as cli

    cli.main(["skill", "install", "kimi"])
    capsys.readouterr()
    cli.main(["status"])
    assert "daimon skill status" not in capsys.readouterr().out


def test_a_body_without_frontmatter_still_gets_stamped():
    """`render_full` always emits frontmatter today. The fallback exists so a
    future variant that does not cannot silently lose its version marker,
    which is the whole thing this issue was filed about."""
    out = skill_install._stamp_full("# just a body\n")
    assert out.startswith(f"<!-- daimon:skill v{__version__} -->")
    assert "# just a body" in out


def test_an_unreadable_block_host_is_reported_not_raised(tmp_path, monkeypatch):
    home = _home(tmp_path)
    skill_install.install("codex", project=False, home=home, cwd=tmp_path)

    def boom(self, *a, **kw):
        raise OSError("nope")

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "codex")
    assert row["state"] == "UNREADABLE"
    assert row["drift"] is True


# ---- rendering ----

def test_the_rich_table_carries_every_row_and_the_repair(tmp_path, monkeypatch,
                                                         capsys):
    """The rich path is what a person actually sees on a terminal, and it is
    a SEPARATE branch from the plain one. A table that silently loses a row
    or a verdict fails exactly where the audit is supposed to speak."""
    from daimon_briefing import render

    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    dest = home / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    dest.write_text("drifted\n", encoding="utf-8")
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_skill_status(skill_install.audit(home=home, cwd=tmp_path))
    out = capsys.readouterr().out
    assert "kimi" in out
    assert "STALE" in out
    assert "1 drifted" in out
    assert "fix: daimon skill install kimi" in out


def test_the_rich_summary_counts_rows_not_just_drift(tmp_path, monkeypatch,
                                                     capsys):
    """#1012: the same compact summary shape the hooks table prints, naming
    the table's own unit - one row per host/scope/skill."""
    from daimon_briefing import render

    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    capsys.readouterr()
    render.render_skill_status(skill_install.audit(home=home, cwd=tmp_path))
    out = capsys.readouterr().out
    report = skill_install.audit(home=home, cwd=tmp_path)
    assert f"{len(report)} rows, 0 drifted" in out


def test_the_plain_path_has_no_summary_line(tmp_path, capsys):
    """The summary is a rich-path addition; plain output stays the
    pre-#1012 contract pipes read."""
    from daimon_briefing import render

    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    capsys.readouterr()
    render.render_skill_status(skill_install.audit(home=home, cwd=tmp_path))
    out = capsys.readouterr().out
    assert "kimi" in out
    assert "rows," not in out


def test_skill_status_cli_rich_summary(tmp_path, monkeypatch, capsys):
    import daimon_briefing.cli as cli
    from daimon_briefing import render

    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("PATH", "")
    assert cli.main(["skill", "install", "kimi"]) == 0
    (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").write_text(
        "drifted\n", encoding="utf-8")
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    capsys.readouterr()
    assert cli.main(["skill", "status"]) == 1
    out = capsys.readouterr().out
    assert "1 drifted" in out
    assert "fix: daimon skill install kimi" in out


def test_the_status_pointer_reaches_the_rich_path_too(monkeypatch, capsys):
    from daimon_briefing import render

    data = {
        "project": "/p/A",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "last": {"result": None, "spawn": None},
        "skill_drift": True,
    }
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status(data)
    assert "daimon skill status" in capsys.readouterr().out


def test_a_probe_that_cannot_read_the_machine_reports_no_drift(monkeypatch):
    """`daimon status` must never crash on a weird tree. A stale skill teaches
    an old protocol; a status command that raises teaches nothing at all."""
    import daimon_briefing.cli as cli

    def boom(**kw):
        raise RuntimeError("weird tree")

    monkeypatch.setattr(skill_install, "audit", boom)
    assert cli._skill_drift_present() is False


# ---- the plugin channel (#1008) ----

def _plugin_registry(home):
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "installed_plugins.json").write_text(
        '{"plugins": {"daimon@daimon": [{"version": "0.43.0"}]}}',
        encoding="utf-8")


def test_a_plugin_served_host_with_no_file_is_not_missing(tmp_path):
    """The plugin serves claude's skill through a different channel. Calling
    that NOT INSTALLED is true of the path and false about the machine."""
    home = _home(tmp_path)
    _plugin_registry(home)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "claude")
    assert row["state"] == "PLUGIN"
    assert row["drift"] is False
    assert row["channel"] == "plugin"


def test_a_plugin_served_row_renders_green_in_the_rich_table(
        tmp_path, monkeypatch, capsys):
    """#1012 follow-up: the shared style map dropped PLUGIN, and a healthy
    plugin-channel machine rendered red - the drift fallback. FORCE_COLOR
    makes rich emit the escapes so the colour is asserted, not implied;
    NO_COLOR and TERM=dumb are cleared because the outer shell may set them
    (the environment quirk behind the two known render-test failures)."""
    from daimon_briefing import render

    home = _home(tmp_path)
    _plugin_registry(home)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    render.render_skill_status(skill_install.audit(home=home, cwd=tmp_path))
    out = capsys.readouterr().out
    assert "\x1b[32mPLUGIN\x1b[0m" in out
    assert "\x1b[31mPLUGIN\x1b[0m" not in out


def test_a_leftover_under_the_plugin_is_named_as_a_leftover(tmp_path):
    """The regression this issue was filed for. An older CLI install leaves a
    skill file behind, the plugin now serves the same skill, and the audit
    called the leftover STALE and told the reader to reinstall it. Following
    that advice produces two copies from two channels, where which one wins is
    an accident of host resolution."""
    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    _plugin_registry(home)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "claude")
    assert row["state"] == "LEFTOVER"
    assert row["channel"] == "plugin"


def test_a_leftover_repairs_by_removal_not_by_install(tmp_path, capsys):
    from daimon_briefing import render

    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    _plugin_registry(home)
    render.render_skill_status(skill_install.audit(home=home, cwd=tmp_path))
    out = capsys.readouterr().out
    assert "daimon skill uninstall claude" in out
    assert "daimon skill install claude" not in out, (
        "the installer refuses to serve a plugin-served host, so advising it "
        "hands the reader a command that declines to run")


def test_a_leftover_counts_as_drift(tmp_path):
    """Two copies of one skill from two channels is a real misconfiguration
    and the repair is a single command, so a provisioning gate should catch
    it. PLUGIN on its own is a healthy machine and stays quiet."""
    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    _plugin_registry(home)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "claude")
    assert row["drift"] is True


def test_no_bundled_rows_are_conjured_under_a_leftover(tmp_path):
    """`daimon-end MISSING` appeared only because the stale main row existed,
    and the printed repair would have created a second copy beside whatever
    the plugin ships."""
    home = _home(tmp_path)
    skill_install.install("claude", project=False, home=home, cwd=tmp_path)
    (home / ".claude" / "skills" / "daimon-end" / "SKILL.md").unlink()
    _plugin_registry(home)
    report = skill_install.audit(home=home, cwd=tmp_path)
    assert not [r for r in report
                if r["host"] == "claude" and r["skill"] == "daimon-end"]


def test_the_plugin_only_speaks_for_the_host_it_serves(tmp_path):
    """One resolver, and it answers for claude alone today. A kimi row must
    never inherit claude's channel."""
    home = _home(tmp_path)
    _plugin_registry(home)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi")
    assert row["state"] == "CURRENT"
    assert row["channel"] == "settings"


def test_the_audit_and_the_installer_agree_on_who_is_served(tmp_path):
    """The whole point of the fix: two commands, one answer. `host_detect`
    learned the plugin channel in #1001 and the audit shipped in #1006 without
    it, so each had its own idea of who serves claude."""
    from daimon_briefing import host_detect

    home = _home(tmp_path)
    _plugin_registry(home)
    found = host_detect.detect(home, kind="skill", path_env="")
    found = host_detect.resolve_channels(found, home=home, kind="skill",
                                         cwd=tmp_path)
    detected = {h.name: h.channel for h in found}
    audited = {r["host"]: r["channel"] for r in skill_install.audit(
        home=home, cwd=tmp_path) if r["skill"] == "daimon"
        and r["scope"] == "global"}
    assert detected["claude"] == audited["claude"] == "plugin"


def test_project_scope_is_never_plugin_served(tmp_path):
    """The plugin is a user-level install. A repo-scoped skill is daimon's own
    file wherever the plugin sits."""
    home = _home(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin_registry(home)
    skill_install.install("claude", project=True, home=home, cwd=repo)
    row = _rows(skill_install.audit(home=home, cwd=repo), "claude",
                scope="project")
    assert row["state"] == "CURRENT"
    assert row["channel"] == "settings"


def test_a_channel_resolver_that_raises_leaves_the_audit_standing(tmp_path,
                                                                  monkeypatch):
    """An audit is what a person runs to find out what is wrong, so it may not
    be the thing that crashes. A channel it cannot resolve degrades to the
    content verdict rather than taking the whole report down."""
    from daimon_briefing import host_detect

    home = _home(tmp_path)
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)

    def boom(_home):
        raise RuntimeError("unreadable registry")

    monkeypatch.setattr(host_detect, "plugin_serves_claude", boom)
    row = _rows(skill_install.audit(home=home, cwd=tmp_path), "kimi")
    assert row["state"] == "CURRENT"
