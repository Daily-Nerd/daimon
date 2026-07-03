"""Subprocess-level tests for the standalone Gemini CLI hook scripts in hook/.

Gemini hook contract (docs/hooks/reference.md, verified 2026-07-01):
- stdout must be PURE JSON ("Silence is Mandatory") — any plain text breaks
  the host's parsing, so every assertion here starts from json.loads(stdout).
- SessionStart context injection rides hookSpecificOutput.additionalContext;
  operator-facing diagnostics ride systemMessage.
- transcript_path is currently an empty stub upstream (gemini-cli#14715), so
  the SessionEnd hook's PRIMARY behavior today is the graceful skip.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from daimon_briefing import store

HOOK_DIR = Path(__file__).parents[2] / "hook"
START_HOOK = HOOK_DIR / "daimon-gemini-session-start.py"
END_HOOK = HOOK_DIR / "daimon-gemini-session-end.py"
MANAGER = HOOK_DIR / "gemini-hooks.py"
LIB_NAME = "_daimon_hook_lib.py"
VENV_BIN = Path(sys.executable).parent


def _copy_without_lib(script: Path, tmp_path) -> Path:
    """Copy a hook into a lib-less dir — the stale/partial-install shape where
    _daimon_hook_lib.py never landed. The same-dir import then fails."""
    stray = tmp_path / "stray"
    stray.mkdir(exist_ok=True)
    dst = stray / script.name
    dst.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _run(script: Path, payload, tmp_path, extra_env=None, args=()) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    if extra_env:
        env.update(extra_env)
    stdin = json.dumps(payload) if isinstance(payload, dict) else (payload or "")
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _additional_context(stdout: str) -> str:
    data = json.loads(stdout)
    return data["hookSpecificOutput"]["additionalContext"]


# --- SessionStart: briefing injection -----------------------------------------


def test_gemini_session_start_emits_additional_context(
    tmp_checkpoint_dir, sample_checkpoint, tmp_path
):
    cwd = "/Users/x/projA"
    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-gemini"
    store.write_checkpoint("S-gemini", mine, project_dir=cwd)

    proc = _run(START_HOOK, {"cwd": cwd, "session_id": "S-new", "source": "startup"}, tmp_path)

    assert proc.returncode == 0
    ctx = _additional_context(proc.stdout)  # implies stdout is pure JSON
    assert "DAIMON BRIEFING" in ctx
    assert "checkpoint: S-gemini" in ctx
    assert "global fallback" not in ctx


def test_gemini_session_start_labels_global_fallback(
    tmp_checkpoint_dir, sample_checkpoint, tmp_path
):
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})

    proc = _run(START_HOOK, {"cwd": "/p/never-seen", "source": "startup"}, tmp_path)

    assert proc.returncode == 0
    ctx = _additional_context(proc.stdout)
    assert "checkpoint: S-global" in ctx
    assert "global fallback" in ctx


def test_gemini_session_start_quiet_when_no_checkpoint(tmp_checkpoint_dir, tmp_path):
    # No checkpoint anywhere: emit NOTHING. Empty stdout is the only other
    # legal output under Gemini's pure-JSON rule.
    proc = _run(START_HOOK, {"cwd": "/p/never-seen", "source": "startup"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_gemini_session_start_missing_cli_emits_install_hint(tmp_checkpoint_dir, tmp_path):
    # Plugin-install onboarding (#91): hooks ship with the plugin, the CLI
    # arrives separately. Hint must be a systemMessage (user-visible), NOT
    # plain stdout text — that would break Gemini's JSON parsing.
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "source": "startup"},
        tmp_path,
        extra_env={"PATH": str(empty_bin)},
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "uv tool install" in data["systemMessage"]
    assert "hookSpecificOutput" not in data


def _fake_cli_recording(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` that appends each invocation's first arg, so a test can
    observe the SessionStart hook spawning `heal` (#26)."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "invocations.txt"
    script = fake_bin / "daimon"
    script.write_text(f"#!/bin/sh\nprintf 'invoked %s\\n' \"$1\" >> \"{capture}\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def test_gemini_session_start_spawns_heal_detached(tmp_path, tmp_checkpoint_dir):
    # #26 parity with the Claude Code and Codex SessionStart hooks.
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "source": "startup"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked heal")
    assert "invoked heal" in content


# --- SessionEnd: serialize spawn ----------------------------------------------


def _fake_cli(tmp_path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "capture.txt"
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "DAIMON_PROJECT_DIR=%s\\nargs=%s\\n" "$DAIMON_PROJECT_DIR" "$*" > "{capture}"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def _wait_for(path: Path, timeout=10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"capture file never appeared: {path}")


def _wait_for_text(path: Path, needle: str, timeout=10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} never appeared in {path}")


def test_gemini_session_end_skips_on_empty_transcript_path(tmp_path, tmp_checkpoint_dir):
    # THE current upstream reality (gemini-cli#14715): transcript_path arrives
    # as an empty stub. The hook must exit 0, spawn nothing, and log the skip.
    fake_bin, capture = _fake_cli(tmp_path)
    payload = {
        "session_id": "S-gemini",
        "transcript_path": "",
        "cwd": "/Users/x/projA",
        "hook_event_name": "SessionEnd",
        "reason": "exit",
    }

    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "gemini-session-end:")
    assert "skipped" in content
    assert "gemini-cli#14715" in content  # point the reader at the upstream stub
    time.sleep(0.3)
    assert not capture.exists()  # nothing was spawned


def test_gemini_session_end_spawns_serialize_when_transcript_present(
    tmp_path, tmp_checkpoint_dir
):
    # The day upstream lands transcript_path, the spawn path must already work.
    fake_bin, capture = _fake_cli(tmp_path)
    transcript = tmp_path / "gemini-session.json"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-gemini",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "hook_event_name": "SessionEnd",
        "reason": "exit",
    }

    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    captured = _wait_for(capture)
    assert "DAIMON_PROJECT_DIR=/Users/x/projA" in captured
    assert str(transcript) in captured
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    _wait_for_text(log, "spawned serialize for S-gemini")


# --- gemini-hooks.py: install | uninstall | status -----------------------------


def _manager(tmp_path, *args) -> subprocess.CompletedProcess:
    return _run(MANAGER, None, tmp_path, args=args)


def test_gemini_hooks_install_uninstall_status_roundtrip(tmp_path):
    settings_path = tmp_path / ".gemini" / "settings.json"
    hooks_dir = tmp_path / ".gemini" / "hooks"

    # install: scripts copied, settings registered
    proc = _manager(tmp_path, "install")
    assert proc.returncode == 0, proc.stderr
    assert (hooks_dir / "daimon-gemini-session-start.py").exists()
    assert (hooks_dir / "daimon-gemini-session-end.py").exists()
    settings = json.loads(settings_path.read_text())
    for event, script in [
        ("SessionStart", "daimon-gemini-session-start.py"),
        ("SessionEnd", "daimon-gemini-session-end.py"),
    ]:
        groups = settings["hooks"][event]
        entries = [h for g in groups for h in g["hooks"] if script in h["command"]]
        assert len(entries) == 1, f"{script} not registered under {event}"
        assert entries[0]["type"] == "command"
        # Gemini timeouts are MILLISECONDS (default 60000) — a Claude-style
        # seconds value (10) would give the hook 10ms and kill every briefing.
        assert entries[0]["timeout"] >= 1000

    # install again: idempotent, no duplicate registration
    proc = _manager(tmp_path, "install")
    assert proc.returncode == 0
    settings = json.loads(settings_path.read_text())
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert len(settings["hooks"]["SessionEnd"]) == 1

    # status: both installed
    proc = _manager(tmp_path, "status")
    assert proc.returncode == 0
    assert proc.stdout.count("installed") >= 2
    assert "not installed" not in proc.stdout

    # uninstall: entries and scripts gone, foreign settings untouched
    proc = _manager(tmp_path, "uninstall")
    assert proc.returncode == 0
    settings = json.loads(settings_path.read_text())
    assert "SessionStart" not in settings.get("hooks", {})
    assert "SessionEnd" not in settings.get("hooks", {})
    assert not (hooks_dir / "daimon-gemini-session-start.py").exists()
    assert not (hooks_dir / "daimon-gemini-session-end.py").exists()

    proc = _manager(tmp_path, "status")
    assert "not installed" in proc.stdout


def test_gemini_hooks_install_preserves_foreign_hooks(tmp_path):
    # A user's pre-existing hook in the same event must survive install+uninstall.
    settings_path = tmp_path / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    foreign = {
        "theme": "dark",
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "echo hi", "name": "mine"}]}
            ]
        },
    }
    settings_path.write_text(json.dumps(foreign))

    assert _manager(tmp_path, "install").returncode == 0
    settings = json.loads(settings_path.read_text())
    assert settings["theme"] == "dark"
    assert len(settings["hooks"]["SessionStart"]) == 2

    assert _manager(tmp_path, "uninstall").returncode == 0
    settings = json.loads(settings_path.read_text())
    assert settings["theme"] == "dark"
    commands = [
        h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]
    ]
    assert commands == ["echo hi"]


def test_gemini_hooks_install_dry_run_writes_nothing(tmp_path):
    proc = _manager(tmp_path, "install", "--dry-run")
    assert proc.returncode == 0
    assert "dry-run" in proc.stdout
    assert not (tmp_path / ".gemini" / "settings.json").exists()


# --- lib-missing fail-open (#108) ----------------------------------------------


def test_gemini_session_start_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    # Stale/partial install: the hook is present but _daimon_hook_lib.py is not.
    # Fail open with a systemMessage (pure JSON, never plain text), exit 0.
    script = _copy_without_lib(START_HOOK, tmp_path)
    proc = _run(script, {"cwd": "/Users/x/projA", "source": "startup"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""  # no traceback
    data = json.loads(proc.stdout)  # implies stdout is pure JSON
    assert "hook library missing" in data["systemMessage"]
    assert "hookSpecificOutput" not in data


def test_gemini_session_end_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    script = _copy_without_lib(END_HOOK, tmp_path)
    transcript = tmp_path / "gemini-session.json"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-gemini",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "reason": "exit",
    }
    proc = _run(script, payload, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # Gemini SessionEnd never prints
    assert proc.stderr.strip() == ""  # no traceback
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "hook library missing")
    assert "gemini-session-end: hook library missing" in content


# --- gemini-hooks.py: shared-library install/uninstall (#108) -------------------


def test_gemini_hooks_install_copies_lib_uninstall_removes_it(tmp_path):
    hooks_dir = tmp_path / ".gemini" / "hooks"
    lib = hooks_dir / LIB_NAME

    assert _manager(tmp_path, "install").returncode == 0
    assert lib.exists()  # copied alongside the scripts
    status = _manager(tmp_path, "status")
    assert LIB_NAME in status.stdout
    assert "not installed" not in status.stdout

    assert _manager(tmp_path, "uninstall").returncode == 0
    assert not lib.exists()  # removed once no daimon hook remains
    assert not (hooks_dir / "daimon-gemini-session-start.py").exists()
