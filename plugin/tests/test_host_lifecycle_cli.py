"""`daimon hooks|skill install|remove` with no host named (#1001).

Detection only applies when no host is named. An explicit host is the old
command, unchanged, and it must stay that way: every runbook, every doc page
and every provisioning script already names one.
"""

import pytest

import daimon_briefing.cli as cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "store"))
    # An empty PATH, or the developer's own machine walks into every test:
    # detection falls back to `shutil.which`, and the box running the suite
    # has claude, codex, gemini and kimi on it.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("daimon_briefing.host_detect.is_interactive",
                        lambda: False)
    return h


def _detected(home, *names):
    for name in names:
        from daimon_briefing.host_detect import HOST_SPECS

        (home / HOST_SPECS[name][0]).mkdir(parents=True, exist_ok=True)


# ---- install with no host named ----

def test_a_single_detected_host_is_installed_without_asking(home, capsys):
    _detected(home, "kimi")
    assert cli.main(["skill", "install"]) == 0
    assert (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()
    out = capsys.readouterr().out
    assert "kimi" in out


def test_an_empty_machine_writes_nothing_and_still_succeeds(home, capsys):
    assert cli.main(["skill", "install"]) == 0
    out = capsys.readouterr().out
    assert "no agent host detected" in out
    assert not (home / ".kimi-code").exists()


def test_several_hosts_with_no_terminal_write_nothing(home, capsys):
    _detected(home, "kimi", "codex")
    assert cli.main(["skill", "install"]) == 0
    out = capsys.readouterr().out
    assert "daimon skill install kimi" in out
    assert "daimon skill install --all" in out
    assert not (home / ".kimi-code" / "skills").exists()
    assert not (home / ".codex" / "AGENTS.md").exists()


def test_all_wires_every_detected_host(home, capsys):
    _detected(home, "kimi", "codex")
    assert cli.main(["skill", "install", "--all"]) == 0
    assert (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()
    assert (home / ".codex" / "AGENTS.md").exists()


def test_the_table_is_printed_before_anything_is_written(home, capsys):
    _detected(home, "kimi", "cursor")
    cli.main(["skill", "install", "--all"])
    out = capsys.readouterr().out
    assert "kimi" in out and "present" in out
    # cursor is detected and has no global skill scope: it must appear in the
    # table saying why, not silently vanish from the report.
    assert "cursor" in out
    assert "--project" in out


def test_project_scope_is_honored_by_the_detected_path(home, tmp_path,
                                                       monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    _detected(home, "cursor")
    assert cli.main(["skill", "install", "--project"]) == 0
    assert (repo / ".cursor" / "rules" / "daimon.mdc").exists()


def test_hooks_install_with_no_host_uses_the_hook_table(home, capsys):
    _detected(home, "kimi")
    assert cli.main(["hooks", "install"]) == 0
    out = capsys.readouterr().out
    assert "kimi" in out


def test_claude_is_reported_as_plugin_served_and_never_written_over(home, capsys):
    _detected(home, "claude")
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True)
    (reg / "installed_plugins.json").write_text(
        '{"plugins": {"daimon@daimon": [{"version": "0.43.0"}]}}',
        encoding="utf-8")
    assert cli.main(["skill", "install"]) == 0
    out = capsys.readouterr().out
    assert "plugin" in out
    assert not (home / ".claude" / "skills" / "daimon" / "SKILL.md").exists()


# ---- the explicit host path is untouched ----

def test_naming_a_host_still_installs_exactly_that_host(home, capsys):
    assert cli.main(["skill", "install", "kimi"]) == 0
    assert (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()


def test_naming_a_host_does_not_require_it_to_be_detected(home):
    """Detection answers "what is here". A named host is an instruction, and
    an operator provisioning a machine before the agent is installed on it is
    a real workflow."""
    assert not (home / ".codex").exists()
    assert cli.main(["skill", "install", "codex"]) == 0
    assert (home / ".codex" / "AGENTS.md").exists()


def test_all_with_a_named_host_refuses(home, capsys):
    rc = cli.main(["skill", "install", "kimi", "--all"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--all" in err
    assert not (home / ".kimi-code").exists()


def test_an_unknown_named_host_still_refuses(home, capsys):
    assert cli.main(["skill", "install", "nosuchhost"]) == 2


# ---- removal ----

def test_removal_with_all_unregisters_only_what_daimon_wrote(home, capsys):
    _detected(home, "kimi")
    cli.main(["hooks", "install", "kimi"])
    capsys.readouterr()
    assert cli.main(["hooks", "remove", "--all"]) == 0
    out = capsys.readouterr().out
    assert "kimi" in out


def test_removal_with_no_terminal_and_no_flag_writes_nothing(home, capsys):
    _detected(home, "kimi")
    cli.main(["skill", "install", "kimi"])
    capsys.readouterr()
    assert cli.main(["skill", "uninstall"]) == 0
    out = capsys.readouterr().out
    assert "daimon skill uninstall kimi" in out
    assert (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()


def test_removal_finds_nothing_when_nothing_is_served(home, capsys):
    _detected(home, "kimi")
    assert cli.main(["skill", "uninstall", "--all"]) == 0
    out = capsys.readouterr().out
    assert "nothing to remove" in out


def test_uninstall_all_removes_every_served_host(home, capsys):
    _detected(home, "kimi", "codex")
    cli.main(["skill", "install", "--all"])
    capsys.readouterr()
    assert cli.main(["skill", "uninstall", "--all"]) == 0
    assert not (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()
    assert "daimon:skill" not in (home / ".codex" / "AGENTS.md").read_text(
        encoding="utf-8")


# ---- exit codes ----

def test_one_failing_host_fails_the_whole_run(home, monkeypatch, capsys):
    """Worst exit code wins. A provisioning script reads the exit code, not
    the prose, so a partial wiring reported as success is a machine everyone
    believes is set up."""
    _detected(home, "kimi", "codex")
    from daimon_briefing import skill_install

    real = skill_install.install

    def flaky(host, **kw):
        if host == "codex":
            raise skill_install.SkillInstallError("broken on purpose")
        return real(host, **kw)

    monkeypatch.setattr(skill_install, "install", flaky)
    assert cli.main(["skill", "install", "--all"]) == 2
    # the healthy host is still wired: one failure must not abandon the rest
    assert (home / ".kimi-code" / "skills" / "daimon" / "SKILL.md").exists()


@pytest.mark.parametrize("argv", [
    ["skill", "uninstall", "kimi", "--all"],
    ["hooks", "install", "kimi", "--all"],
    ["hooks", "remove", "kimi", "--all"],
])
def test_all_and_a_named_host_are_refused_on_every_verb(home, capsys, argv):
    assert cli.main(argv) == 2
    assert "--all" in capsys.readouterr().err


def test_uninstalling_an_unknown_named_host_refuses(home, capsys):
    assert cli.main(["skill", "uninstall", "nosuchhost"]) == 2
    assert "unknown host" in capsys.readouterr().err
