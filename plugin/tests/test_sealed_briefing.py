"""Sealed checkout diagnostics belong to the current briefing response."""

import time

import pytest

from daimon_briefing import briefing, cli, hooks, hostgit, mcp_tools, store
from tests.test_hostgit_config_gate import _plain
from tests.test_hostgit_sealed import fake_repo


@pytest.fixture
def sealed(tmp_path):
    repo = fake_repo(tmp_path / "repo")
    hostgit.configure(posture="sealed", ceiling=tmp_path)
    return repo


def refuse(repo, key="core.pager"):
    assert _plain(repo, "config", key, "value").returncode == 0


@pytest.mark.parametrize("branch", ["main", "feature/topic", "release.1"])
def test_vscode_metadata_cache_tracks_config_changes(sealed, branch):
    assert isinstance(hostgit.project(sealed), hostgit.Checkout)
    refuse(sealed, f"branch.{branch}.vscode-merge-base")
    assert isinstance(hostgit.project(sealed), hostgit.Checkout)
    refuse(sealed)
    assert hostgit.project(sealed) == hostgit.Refused("config-key:core.pager")
    assert _plain(sealed, "config", "--unset", "core.pager").returncode == 0
    assert isinstance(hostgit.project(sealed), hostgit.Checkout)


@pytest.mark.parametrize("key", ["branch..vscode-merge-base",
                                  "branch.main.vscode-merge-base-extra",
                                  "branch.main.vscode-mergebase",
                                  "branch.main.vscode-other"])
def test_near_match_metadata_stays_refused(key):
    assert not hostgit.config_key_allowed(key)


def test_no_checkpoint_normal_entry_points(sealed, monkeypatch, capsys):
    refuse(sealed)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(sealed))
    expected = "config-key:core.pager"
    assert cli.main(["brief", "--project", str(sealed)]) == 0
    assert expected in capsys.readouterr().out
    got = briefing.render_briefing(sealed)
    assert got.state == "no-checkpoint"
    assert got.text.count(expected) == 1
    out = mcp_tools.HANDLERS["daimon_brief"]({"project": str(sealed)}).text
    assert out.count(expected) == 1
    ctx = hooks.pre_llm_call(session_id="new", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert ctx and ctx["context"].count(expected) == 1


def test_repeated_projects_and_repaired_config_do_not_leak(sealed, tmp_path):
    other = fake_repo(tmp_path / "other")
    refuse(other, "core.editor")
    hostgit.project(other)
    refuse(sealed)
    for _ in range(2):
        got = briefing.render_briefing(sealed)
        assert got.text.count("config-key:core.pager") == 1
        assert "core.editor" not in got.text
        assert str(other) not in got.text
    assert _plain(sealed, "config", "--unset", "core.pager").returncode == 0
    assert "Git-backed reads unavailable" not in briefing.render_briefing(sealed).text


def test_no_checkpoint_subdirectory_keeps_refusal(sealed, capsys):
    refuse(sealed)
    subdir = sealed / "nested"
    subdir.mkdir()
    assert cli.main(["brief", "--project", str(subdir)]) == 0
    assert "config-key:core.pager" in capsys.readouterr().out


def test_refusal_reason_is_bounded_and_control_safe(sealed, monkeypatch):
    real = hostgit.project
    reason = "config-key:branch.bad\x1b[31m\nforged\x07" + "x" * 10000
    monkeypatch.setattr(hostgit, "project", lambda path: hostgit.Refused(reason))
    got = briefing.prepare(str(sealed), time.time(), worldcheck_project=str(sealed))
    assert len(got.notes) == 1
    assert len(got.notes[0].encode()) < 600
    assert "\x1b" not in got.notes[0] and "\n" not in got.notes[0]
    assert "\x07" not in got.notes[0]
    monkeypatch.setattr(hostgit, "project", real)


def test_ambient_and_named_bucket_do_not_query_diagnostic(sealed, monkeypatch):
    refuse(sealed)
    hostgit.configure(posture="ambient")
    assert "Git-backed reads unavailable" not in briefing.render_briefing(sealed).text
    hostgit.configure(posture="sealed")
    calls = []
    real = hostgit.project

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(hostgit, "project", spy)
    got = briefing.machine_brief("named-bucket", time.time(), loops_pointer=False)
    assert "Git-backed reads unavailable" not in got.text
    assert calls == []


def test_checkpoint_body_keeps_diagnostic_within_budget(
        sealed, sample_checkpoint, monkeypatch, capsys):
    from daimon_briefing.surfaces import Writer

    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=str(sealed),
                           writer=Writer.HUMAN)
    refuse(sealed)
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "1800")
    assert cli.main(["brief", "--label", "--project", str(sealed)]) == 0
    text = capsys.readouterr().out
    assert text.count("config-key:core.pager") == 1
    assert len(text.encode()) <= 1800
    assert "Wiring the on_session_end hook" in text
    got = briefing.render_briefing(sealed)
    assert got.state == "briefing"
    assert got.text.count("config-key:core.pager") == 1
    assert len(got.text.encode()) <= 1800


def test_header_only_fallback_reports_requested_room(
        sealed, tmp_path, sample_checkpoint, capsys):
    from daimon_briefing.surfaces import Writer

    other = fake_repo(tmp_path / "foreign")
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=str(other),
                           writer=Writer.HUMAN)
    refuse(other, "core.editor")
    hostgit.project(other)
    refuse(sealed)
    assert cli.main(["brief", "--project", str(sealed)]) == 0
    text = capsys.readouterr().out
    assert text.count("config-key:core.pager") == 1
    assert str(sealed)[:80] in text
    assert "core.editor" not in text
    assert "Wiring the on_session_end hook" not in text


def test_worldcheck_keeps_git_refused_without_gh_probe(
        sealed, sample_checkpoint, monkeypatch):
    from daimon_briefing import worldcheck

    refuse(sealed)
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: "/fake/gh")
    calls = []

    def spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError("refused evidence must never launch gh")

    monkeypatch.setattr(hostgit, "gh_popen", spawn)
    claim = sample_checkpoint["working_context"]["open_questions"][0]
    claim.update(text="PR #6 is merged", carried_from="S-earlier", id="o-000001")
    reasons = []
    stats = worldcheck.check(sample_checkpoint, str(sealed), skip_reasons=reasons)
    assert reasons == ["pr-state:skipped-git-refused"]
    assert stats["confirmed"] == stats["contradicted"] == 0
    assert stats["skipped"] == 1
    assert calls == []


def test_checkout_diagnostic_survives_note_cap(sealed, monkeypatch):
    from daimon_briefing import display, ledger

    refuse(sealed)
    health = tuple(f"health note {i}" for i in range(display.NOTE_CAP + 2))
    monkeypatch.setattr(ledger, "admission_notes", lambda slug: health)
    got = briefing.prepare(str(sealed), time.time())
    assert "config-key:core.pager" in got.notes[0]
    assert len(got.notes) == display.NOTE_CAP + 1
    assert all(line in display.all_notes(got.notes) for line in health)


@pytest.mark.parametrize("script", ["daimon-session-brief.py",
                                    "daimon-codex-session-start.py",
                                    "daimon-gemini-session-start.py",
                                    "daimon-kimi-user-prompt-submit.py"])
def test_host_adapter_preserves_day_one_warning(sealed, capsys, script):
    import runpy
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    refuse(sealed)
    assert cli.main(["brief", "--project", str(sealed)]) == 0
    text = capsys.readouterr().out
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                   / "hook" / script))
    adapter = namespace.get("_emit_briefing") or namespace["_brief"]
    calls = []

    def run_brief(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, stdout=text, stderr="")

    adapter.__globals__["lib"] = SimpleNamespace(run_brief=run_brief)
    if script.startswith("daimon-kimi"):
        output = adapter("daimon", str(sealed))
    else:
        adapter(str(sealed), "daimon")
        output = capsys.readouterr().out
    assert len(calls) == 1
    assert output.count("config-key:core.pager") == 1


def test_no_checkpoint_default_budget_bounds_hostile_fields(sealed, monkeypatch, capsys):
    monkeypatch.setattr(hostgit, "project", lambda path: hostgit.Refused(
        "config-key:branch.bad\x1b[31m\nforged\x07" + "x" * 10000))
    assert cli.main(["brief", "--project", str(sealed)]) == 0
    text = capsys.readouterr().out
    assert len(text.encode()) <= 9728
    assert "\x1b" not in text and "\x07" not in text
    assert text.startswith("⚠ Git-backed reads unavailable")


def test_room_path_controls_are_safe_and_bounded(tmp_path):
    repo = fake_repo(tmp_path / "room\n\x1b[31m")
    hostgit.configure(posture="sealed", ceiling=tmp_path)
    refuse(repo)
    got = briefing.render_briefing(repo)
    warning = got.notes[0]
    assert "config-key:core.pager" in warning
    assert "\n" not in warning and "\x1b" not in warning
    assert len(warning.encode()) < 600
