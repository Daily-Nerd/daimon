"""Detection-driven install lifecycle (#1001).

Two halves, tested apart because they fail apart. `detect` and
`resolve_channels` are pure reads: they answer "what is on this machine and
who already serves it" and they must never write, never prompt, and never
raise on another tool's file format. `decide` is the policy: given that
picture, which hosts get written, and what does the operator get told when
the answer is none.
"""

from pathlib import Path

import pytest

from daimon_briefing import host_detect


def _home(tmp_path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _by_name(found):
    return {h.name: h for h in found}


# ---- detection is a pure read ----

def test_an_empty_machine_detects_nothing(tmp_path):
    found = host_detect.detect(_home(tmp_path), kind="skill", path_env="")
    assert found, "the host table itself must still be reported"
    assert not any(h.present for h in found)
    assert all(h.channel == "none" for h in found)


def test_a_config_directory_is_the_signal(tmp_path):
    home = _home(tmp_path)
    (home / ".kimi-code").mkdir()
    found = _by_name(host_detect.detect(home, kind="skill", path_env=""))
    assert found["kimi"].present
    assert str(home / ".kimi-code") == found["kimi"].signal
    assert not found["codex"].present


def test_a_binary_on_path_is_the_signal_when_no_config_dir_exists(tmp_path):
    home = _home(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "codex"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    found = _by_name(host_detect.detect(home, kind="skill", path_env=str(bindir)))
    assert found["codex"].present
    assert found["codex"].signal == "codex on PATH"


def test_detection_writes_nothing(tmp_path):
    """Detection runs before any consent has been given, so it may not create
    the very directories it is looking for. A probe that mkdirs its own answer
    reports every host as present on the second run."""
    home = _home(tmp_path)
    (home / ".claude").mkdir()
    before = sorted(p.relative_to(home) for p in home.rglob("*"))
    host_detect.detect(home, kind="hook", path_env="")
    host_detect.detect(home, kind="skill", path_env="")
    assert sorted(p.relative_to(home) for p in home.rglob("*")) == before


# ---- wirability is per kind, and per scope ----

def test_claude_hooks_are_not_wirable_because_the_plugin_ships_them(tmp_path):
    home = _home(tmp_path)
    (home / ".claude").mkdir()
    claude = _by_name(host_detect.detect(home, kind="hook", path_env=""))["claude"]
    assert claude.present
    assert not claude.wirable
    assert claude.hint and "plugin" in claude.hint


def test_hook_wirability_matches_the_hook_host_table():
    import daimon_briefing.cli as _cli

    found = _by_name(host_detect.detect(Path("/nonexistent"), kind="hook",
                                        path_env=""))
    for name, host in found.items():
        assert host.wirable == (name in _cli._HOOK_HOSTS), name


def test_cursor_has_no_global_skill_scope_but_does_have_a_project_one(tmp_path):
    home = _home(tmp_path)
    glob = _by_name(host_detect.detect(home, kind="skill", path_env=""))["cursor"]
    proj = _by_name(host_detect.detect(home, kind="skill", project=True,
                                       path_env=""))["cursor"]
    assert not glob.wirable
    assert glob.hint and "--project" in glob.hint
    assert proj.wirable


def test_only_a_host_daimon_registered_is_removable(tmp_path):
    """`hooks remove` unregisters what daimon wrote. For every other host the
    registration is a snippet a person pasted somewhere daimon cannot see, so
    offering to remove it would be a verb that reports success having touched
    nothing."""
    found = _by_name(host_detect.detect(Path("/nonexistent"), kind="hook-remove",
                                        path_env=""))
    assert found["kimi"].wirable
    assert not found["codex"].wirable
    assert found["codex"].hint


# ---- channels ----

def test_an_installed_plugin_makes_claude_plugin_served(tmp_path):
    home = _home(tmp_path)
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True)
    (reg / "installed_plugins.json").write_text(
        '{"plugins": {"daimon@daimon": [{"version": "0.43.0"}]}}', encoding="utf-8")
    found = host_detect.detect(home, kind="skill", path_env="")
    found = _by_name(host_detect.resolve_channels(found, home=home, kind="skill",
                                                  cwd=tmp_path))
    assert found["claude"].channel == "plugin"


def test_a_missing_or_unreadable_registry_is_never_plugin(tmp_path):
    home = _home(tmp_path)
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True)
    (reg / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    found = host_detect.detect(home, kind="skill", path_env="")
    found = _by_name(host_detect.resolve_channels(found, home=home, kind="skill",
                                                  cwd=tmp_path))
    assert found["claude"].channel == "none"


def test_an_installed_skill_file_is_the_settings_channel(tmp_path):
    from daimon_briefing import skill_install

    home = _home(tmp_path)
    (home / ".kimi-code").mkdir()
    skill_install.install("kimi", project=False, home=home, cwd=tmp_path)
    found = host_detect.detect(home, kind="skill", path_env="")
    found = _by_name(host_detect.resolve_channels(found, home=home, kind="skill",
                                                  cwd=tmp_path))
    assert found["kimi"].channel == "settings"


def test_an_absent_host_is_never_given_a_channel(tmp_path):
    home = _home(tmp_path)
    found = host_detect.detect(home, kind="skill", path_env="")
    found = _by_name(host_detect.resolve_channels(found, home=home, kind="skill",
                                                  cwd=tmp_path))
    assert all(h.channel == "none" for h in found.values())


# ---- the decision ----

def _host(name, *, present=True, wirable=True, channel="none"):
    return host_detect.Host(name=name, present=present,
                            signal="signal" if present else "",
                            wirable=wirable, channel=channel)


def _never(question):  # pragma: no cover - guard, must never be reached
    raise AssertionError(f"asked when it must not: {question}")


def test_nothing_detected_says_so_and_writes_nothing():
    found = [_host("claude", present=False), _host("kimi", present=False)]
    d = host_detect.decide(found, interactive=True, all_flag=False, ask=_never,
                           command="skill install")
    assert d.install == []
    assert any("no agent host detected" in line for line in d.lines)


def test_every_detected_host_already_served_is_a_different_answer():
    """An empty machine and a fully served one are two different answers.
    Blaming the plugin when nothing was detected sends the reader looking for
    a plugin that is not there."""
    found = [_host("claude", channel="plugin")]
    d = host_detect.decide(found, interactive=True, all_flag=False, ask=_never,
                           command="skill install")
    assert d.install == []
    assert any("already served" in line for line in d.lines)
    assert not any("no agent host detected" in line for line in d.lines)


def test_a_single_host_installs_without_asking():
    found = [_host("kimi"), _host("codex", present=False)]
    d = host_detect.decide(found, interactive=True, all_flag=False, ask=_never,
                           command="skill install")
    assert d.install == ["kimi"]


def test_several_hosts_on_a_terminal_take_one_confirmation_for_all():
    """One question, not one per host. The operator is answering `wire this
    machine`, and a per-host interrogation turns a single decision into five."""
    asked = []

    def ask(question):
        asked.append(question)
        return True

    found = [_host("kimi"), _host("codex"), _host("windsurf")]
    d = host_detect.decide(found, interactive=True, all_flag=False, ask=ask,
                           command="skill install")
    assert len(asked) == 1
    assert "codex" in asked[0] and "kimi" in asked[0] and "windsurf" in asked[0]
    assert d.install == ["codex", "kimi", "windsurf"]


def test_a_declined_confirmation_writes_nothing():
    found = [_host("kimi"), _host("codex")]
    d = host_detect.decide(found, interactive=True, all_flag=False,
                           ask=lambda _q: False, command="skill install")
    assert d.install == []


def test_several_hosts_with_no_terminal_print_the_explicit_commands():
    found = [_host("kimi"), _host("codex")]
    d = host_detect.decide(found, interactive=False, all_flag=False, ask=_never,
                           command="skill install")
    assert d.install == []
    joined = "\n".join(d.lines)
    assert "daimon skill install codex" in joined
    assert "daimon skill install kimi" in joined
    assert "daimon skill install --all" in joined


def test_the_all_flag_answers_for_every_detected_host():
    found = [_host("kimi"), _host("codex"), _host("cursor", wirable=False)]
    d = host_detect.decide(found, interactive=False, all_flag=True, ask=_never,
                           command="skill install")
    assert d.install == ["codex", "kimi"]


def test_a_plugin_served_host_is_never_written_over_even_with_all():
    found = [_host("claude", channel="plugin"), _host("kimi")]
    d = host_detect.decide(found, interactive=False, all_flag=True, ask=_never,
                           command="skill install")
    assert d.install == ["kimi"]
    assert any("claude" in line and "plugin" in line for line in d.lines)


def test_a_host_already_served_manually_is_still_refreshed():
    """Re-running install is the documented post-upgrade step: the installed
    skill is a static copy of content that moves with the CLI. A host on the
    settings channel must stay reachable without a flag."""
    found = [_host("kimi", channel="settings")]
    d = host_detect.decide(found, interactive=True, all_flag=False, ask=_never,
                           command="skill install")
    assert d.install == ["kimi"]


# ---- removal is never unasked ----

def test_removal_acts_only_on_hosts_that_are_actually_served():
    found = [_host("kimi", channel="settings"), _host("codex")]
    d = host_detect.decide_removal(found, interactive=False, all_flag=True,
                                   ask=_never, command="hooks remove")
    assert d.install == ["kimi"]


def test_removal_without_the_flag_or_a_terminal_writes_nothing():
    found = [_host("kimi", channel="settings")]
    d = host_detect.decide_removal(found, interactive=False, all_flag=False,
                                   ask=_never, command="hooks remove")
    assert d.install == []
    assert any("daimon hooks remove kimi" in line for line in d.lines)


def test_a_single_served_host_is_still_confirmed_before_removal():
    """The install ladder installs a lone host without asking, because writing
    a skill file is what the operator asked for. Removal is the other
    direction and gets its own confirmation even when there is only one
    candidate."""
    asked = []
    found = [_host("kimi", channel="settings")]
    d = host_detect.decide_removal(found, interactive=True, all_flag=False,
                                   ask=lambda q: asked.append(q) or True,
                                   command="hooks remove")
    assert len(asked) == 1
    assert d.install == ["kimi"]


def test_nothing_served_is_reported_as_nothing_to_remove():
    found = [_host("kimi"), _host("codex")]
    d = host_detect.decide_removal(found, interactive=True, all_flag=True,
                                   ask=_never, command="hooks remove")
    assert d.install == []
    assert any("nothing to remove" in line for line in d.lines)


# ---- rendering ----

def test_the_table_names_the_channel_and_the_signal():
    found = [_host("kimi", channel="settings"),
             _host("codex", present=False),
             _host("cursor", wirable=False)]
    table = host_detect.render_table(found)
    text = "\n".join(table)
    assert "kimi" in text and "settings" in text
    assert "absent" in text
    assert "not wirable" in text


def test_worst_exit_code_wins():
    """One host failing is reported as failure, never masked by the others
    succeeding. A provisioning script reads the exit code, not the prose."""
    calls = []

    def runner(name):
        calls.append(name)
        return {"kimi": 0, "codex": 1, "windsurf": 0}[name]

    rc = host_detect.run_all(["kimi", "codex", "windsurf"], runner)
    assert rc == 1
    assert calls == ["kimi", "codex", "windsurf"], "every host still runs"


def test_run_all_on_an_empty_list_is_success():
    assert host_detect.run_all([], _never) == 0


@pytest.mark.parametrize("kind", ["hook", "hook-remove", "skill"])
def test_every_kind_reports_the_same_host_table(kind):
    """The operator sees one machine. A host that vanishes from the table
    under one verb reads as "not installed here" rather than "this verb
    cannot serve it", which is the exact confusion the wirable flag and its
    hint exist to prevent."""
    names = [h.name for h in host_detect.detect(Path("/nonexistent"), kind=kind,
                                                path_env="")]
    assert names == sorted(host_detect.HOST_SPECS)


# ---- a read never crashes on another tool's tree ----

def test_an_unreadable_config_path_is_absent_not_an_exception(tmp_path, monkeypatch):
    """Detection walks paths other tools own. A permission error or a broken
    symlink there is a host that is not usable, not a traceback out of a
    read-only probe."""
    def boom(self):
        raise OSError("nope")

    monkeypatch.setattr(Path, "is_dir", boom)
    found = host_detect.detect(_home(tmp_path), kind="skill", path_env="")
    assert not any(h.present for h in found)


def test_a_registry_whose_plugins_key_is_not_a_mapping_is_never_plugin(tmp_path):
    home = _home(tmp_path)
    reg = home / ".claude" / "plugins"
    reg.mkdir(parents=True)
    (reg / "installed_plugins.json").write_text('{"plugins": ["daimon"]}',
                                                encoding="utf-8")
    assert host_detect.plugin_serves_claude(home) is False


def test_a_hook_report_that_raises_leaves_every_channel_unset(tmp_path, monkeypatch):
    import daimon_briefing.cli as _cli

    home = _home(tmp_path)
    (home / ".kimi-code").mkdir()

    def boom(_home):
        raise RuntimeError("weird hooks tree")

    monkeypatch.setattr(_cli, "_hooks_status_report", boom)
    found = host_detect.detect(home, kind="hook", path_env="")
    found = _by_name(host_detect.resolve_channels(found, home=home, kind="hook",
                                                  cwd=tmp_path))
    assert found["kimi"].channel == "none"


def test_a_host_with_no_scope_never_claims_a_channel(tmp_path):
    """cursor has no global skill destination. If something ever marks it
    wirable at that scope, the channel lookup must still answer "no", not
    index a row that is not there."""
    home = _home(tmp_path)
    (home / ".cursor").mkdir()
    forced = [host_detect.Host(name="cursor", present=True, signal="x",
                               wirable=True)]
    resolved = host_detect.resolve_channels(forced, home=home, kind="skill",
                                            cwd=tmp_path)
    assert resolved[0].channel == "none"


def test_an_unreadable_skill_destination_is_not_installed(tmp_path, monkeypatch):
    home = _home(tmp_path)
    (home / ".kimi-code").mkdir()

    def boom(self):
        raise OSError("nope")

    found = host_detect.detect(home, kind="skill", path_env="")
    monkeypatch.setattr(Path, "exists", boom)
    resolved = _by_name(host_detect.resolve_channels(found, home=home,
                                                     kind="skill", cwd=tmp_path))
    assert resolved["kimi"].channel == "none"


def test_a_host_daimon_ships_nothing_for_says_so(tmp_path, monkeypatch):
    """HOST_SPECS is what daimon looks for; the skill and hook tables are what
    it can write. A host in the first and neither of the others must still be
    listed, with the reason."""
    monkeypatch.setitem(host_detect.HOST_SPECS, "opencode",
                        (".config/opencode", "opencode"))
    home = _home(tmp_path)
    found = _by_name(host_detect.detect(home, kind="skill", path_env=""))
    assert found["opencode"].wirable is False
    assert "ships no skill" in found["opencode"].hint
    found = _by_name(host_detect.detect(home, kind="hook", path_env=""))
    assert "ships no hooks" in found["opencode"].hint


# ---- prompting ----

def test_the_prompt_accepts_only_an_explicit_yes(monkeypatch):
    answers = iter(["y", "YES", " yes ", "n", "", "maybe"])
    monkeypatch.setattr("builtins.input", lambda _p: next(answers))
    assert host_detect.ask_yes_no("q") is True
    assert host_detect.ask_yes_no("q") is True
    assert host_detect.ask_yes_no("q") is True
    assert host_detect.ask_yes_no("q") is False
    assert host_detect.ask_yes_no("q") is False
    assert host_detect.ask_yes_no("q") is False


def test_a_closed_stdin_is_a_no(monkeypatch):
    def boom(_p):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert host_detect.ask_yes_no("q") is False


def test_interactive_needs_both_ends_of_the_terminal(monkeypatch):
    class Fake:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    monkeypatch.setattr(host_detect.sys, "stdin", Fake(True))
    monkeypatch.setattr(host_detect.sys, "stdout", Fake(False))
    assert host_detect.is_interactive() is False
    monkeypatch.setattr(host_detect.sys, "stdout", Fake(True))
    assert host_detect.is_interactive() is True


def test_a_declined_removal_writes_nothing():
    found = [_host("kimi", channel="settings"), _host("codex", channel="settings")]
    d = host_detect.decide_removal(found, interactive=True, all_flag=False,
                                   ask=lambda _q: False, command="hooks remove")
    assert d.install == []
    assert any("declined" in line for line in d.lines)
