"""Subprocess-level tests for the standalone Codex hook scripts in hook/."""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from daimon_briefing import store

HOOK_DIR = Path(__file__).parents[2] / "hook"
START_HOOK = HOOK_DIR / "daimon-codex-session-start.py"
STOP_HOOK = HOOK_DIR / "daimon-codex-stop.py"
MANAGER = HOOK_DIR / "codex-hooks.py"
LIB_NAME = "_daimon_hook_lib.py"
VENV_BIN = Path(sys.executable).parent


def _manager(tmp_path, *args) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, str(MANAGER), *args],
        input="", capture_output=True, text=True, env=env, timeout=30,
    )


def _copy_without_lib(script: Path, tmp_path) -> Path:
    """Copy a hook into a lib-less dir — the stale/partial-install shape where
    _daimon_hook_lib.py never landed. The same-dir import then fails."""
    stray = tmp_path / "stray"
    stray.mkdir(exist_ok=True)
    dst = stray / script.name
    dst.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _run(script: Path, payload, tmp_path, extra_env=None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    if extra_env:
        env.update(extra_env)
    stdin = json.dumps(payload) if isinstance(payload, dict) else (payload or "")
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _additional_context(stdout: str) -> str:
    data = json.loads(stdout)
    return data["hookSpecificOutput"]["additionalContext"]


def test_codex_session_start_emits_additional_context(
    tmp_checkpoint_dir, sample_checkpoint, tmp_path
):
    cwd = "/Users/x/projA"
    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-codex"
    store.write_checkpoint("S-codex", mine, project_dir=cwd)

    proc = _run(START_HOOK, {"cwd": cwd, "session_id": "S-new"}, tmp_path)

    assert proc.returncode == 0
    ctx = _additional_context(proc.stdout)
    assert "DAIMON BRIEFING" in ctx
    assert "checkpoint: S-codex" in ctx
    assert "global fallback" not in ctx


def test_codex_session_start_labels_global_fallback(
    tmp_checkpoint_dir, sample_checkpoint, tmp_path
):
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})

    proc = _run(START_HOOK, {"cwd": "/p/never-seen"}, tmp_path)

    assert proc.returncode == 0
    ctx = _additional_context(proc.stdout)
    assert "checkpoint: S-global" in ctx
    assert "global fallback" in ctx


def _fake_cli_recording(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` that appends each invocation's first arg, so a
    test can observe the SessionStart hook spawning `heal` (#26)."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "invocations.txt"
    script = fake_bin / "daimon"
    script.write_text(f"#!/bin/sh\nprintf 'invoked %s\\n' \"$1\" >> \"{capture}\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def test_codex_session_start_spawns_heal_detached(tmp_path, tmp_checkpoint_dir):
    # #26: Codex SessionStart opportunistically fires `daimon heal`.
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked heal")
    assert "invoked heal" in content


def test_codex_session_start_resolves_deprecated_alias(tmp_path, tmp_checkpoint_dir):
    # Deprecated-alias transition: only `daimon-briefing` is on PATH (no `daimon`).
    # The hook must still resolve and spawn it (preferred-then-fallback resolution).
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "invocations.txt"
    script = fake_bin / "daimon-briefing"
    script.write_text(f"#!/bin/sh\nprintf 'invoked %s\\n' \"$1\" >> \"{capture}\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked heal")
    assert "invoked heal" in content


def test_codex_session_start_disable_suppresses_heal(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA"},
        tmp_path,
        extra_env={"PATH": str(fake_bin), "DAIMON_DISABLE": "1"},
    )
    assert proc.returncode == 0
    time.sleep(0.3)
    assert not capture.exists()


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


# ---- orphan catch-up sweep (#188): SessionStart wiring to lib.sweep_orphans ----
#
# lib.sweep_orphans itself (newest-only, 14-day cutoff, checkpoint-freshness
# skip, directory-missing no-op, spawn-failure fail-open) is already
# exhaustively unit-tested in test_claude_hooks.py against the exact same
# shared function — it is reused here unmodified, not copied. These tests
# only cover what is specific to the Codex hook: that SessionStart actually
# wires session_id/transcript_path through to the sweep, runs it after heal,
# and never lets it disturb the briefing.


def _age(path: Path, seconds: float) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def _fake_cli_argv_recording(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` that appends each invocation's full argv to a capture
    file (one line per call), so a test can distinguish the `heal` call from
    a `serialize <path>` call spawned later by the same hook run."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "argv.txt"
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{capture}"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def test_codex_session_start_spawns_catch_up_serialize_for_orphaned_transcript(
    tmp_path, tmp_checkpoint_dir
):
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)  # 1h old, no checkpoint anywhere -> orphan

    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "transcript_path": str(current)},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "serialize")
    serialize_calls = [
        line for line in content.splitlines() if line.split()[:1] == ["serialize"]
    ]
    assert len(serialize_calls) == 1
    assert str(orphan) in serialize_calls[0]


def test_codex_session_start_no_catch_up_spawn_without_orphan(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")

    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "transcript_path": str(current)},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    _wait_for_text(capture, "heal")  # heal always fires — confirms the child had time to run
    content = capture.read_text()
    serialize_calls = [
        line for line in content.splitlines() if line.split()[:1] == ["serialize"]
    ]
    assert serialize_calls == []


def test_codex_session_start_excludes_current_session_via_session_id(
    tmp_path, tmp_checkpoint_dir
):
    # The current session's OWN transcript must never be swept, even aged and
    # uncaptured — proves the hook actually threads session_id through to
    # lib.sweep_orphans rather than relying on path identity alone.
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    _age(current, 3600)

    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "transcript_path": str(current)},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    _wait_for_text(capture, "heal")
    content = capture.read_text()
    serialize_calls = [
        line for line in content.splitlines() if line.split()[:1] == ["serialize"]
    ]
    assert serialize_calls == []


def test_codex_session_start_sweep_noop_when_transcript_dir_missing(tmp_path, tmp_checkpoint_dir):
    # Codex's transcript_path layout is documented as not a stable interface
    # (hook/CODEX.md) — a sibling directory that doesn't exist must be a silent
    # no-op, never a crash.
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    missing = tmp_path / "gone" / "S-new.jsonl"

    proc = _run(
        START_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new", "transcript_path": str(missing)},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    _wait_for_text(capture, "heal")
    content = capture.read_text()
    serialize_calls = [
        line for line in content.splitlines() if line.split()[:1] == ["serialize"]
    ]
    assert serialize_calls == []


def test_codex_session_start_sweep_never_breaks_briefing_output(
    tmp_path, tmp_checkpoint_dir, sample_checkpoint
):
    # No transcript_path in the payload at all -> sweep can't act, briefing is
    # unaffected (matches every pre-#188 session-start test's payload shape).
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    proc = _run(START_HOOK, {"cwd": "/p/never-seen", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    ctx = _additional_context(proc.stdout)
    assert "checkpoint: S-global" in ctx


def _wait_for(path: Path, timeout=10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"capture file never appeared: {path}")


def test_codex_stop_passes_project_dir_to_child(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli(tmp_path)
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-codex",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "hook_event_name": "Stop",
    }

    proc = _run(
        STOP_HOOK,
        payload,
        tmp_path,
        extra_env={
            "PATH": str(fake_bin),
            "DAIMON_CODEX_MIN_SERIALIZE_INTERVAL": "0",
        },
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    captured = _wait_for(capture)
    assert "DAIMON_PROJECT_DIR=/Users/x/projA" in captured
    assert str(transcript) in captured


def _fake_cli_stdout(tmp_path, sentinel: str) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    script = fake_bin / "daimon"
    script.write_text(f"#!/bin/sh\necho '{sentinel} wrote checkpoint: /x (took 1s)'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin


def _wait_for_text(path: Path, needle: str, timeout=10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} never appeared in {path}")


def test_codex_stop_does_not_route_child_stdout_into_log(tmp_path, tmp_checkpoint_dir):
    # FR #27: results are logged first-class by the CLI; the Codex hook must not
    # capture child stdout into serialize.log. Only the spawn line lands there.
    sentinel = "CHILD-STDOUT-SENTINEL"
    fake_bin = _fake_cli_stdout(tmp_path, sentinel)
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-codex",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "hook_event_name": "Stop",
    }
    proc = _run(
        STOP_HOOK,
        payload,
        tmp_path,
        extra_env={"PATH": str(fake_bin), "DAIMON_CODEX_MIN_SERIALIZE_INTERVAL": "0"},
    )
    assert proc.returncode == 0
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    _wait_for_text(log, "spawned serialize for S-codex")
    time.sleep(0.3)  # give the detached child a chance to (not) leak its stdout
    content = log.read_text()
    assert "codex-stop: spawned serialize for S-codex" in content
    assert sentinel not in content


def test_codex_stop_throttles_repeated_serializes(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli(tmp_path)
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-codex",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "hook_event_name": "Stop",
    }
    env = {
        "PATH": str(fake_bin),
        "DAIMON_CODEX_MIN_SERIALIZE_INTERVAL": "300",
    }

    first = _run(STOP_HOOK, payload, tmp_path, extra_env=env)
    assert first.returncode == 0
    _wait_for(capture)
    capture.unlink()

    second = _run(STOP_HOOK, payload, tmp_path, extra_env=env)

    assert second.returncode == 0
    time.sleep(0.2)
    assert not capture.exists()


# ---- lib-missing fail-open (#108) ----


def test_codex_session_start_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    # Stale/partial install: the hook is present but _daimon_hook_lib.py is not.
    # Fail open with a pure-JSON diagnostic (never plain text), exit 0.
    script = _copy_without_lib(START_HOOK, tmp_path)
    proc = _run(script, {"cwd": "/Users/x/projA", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""  # no traceback
    ctx = _additional_context(proc.stdout)  # implies stdout is pure JSON
    assert "hook library missing" in ctx


def test_codex_stop_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    script = _copy_without_lib(STOP_HOOK, tmp_path)
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-codex",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "hook_event_name": "Stop",
    }
    proc = _run(
        script, payload, tmp_path,
        extra_env={"DAIMON_CODEX_MIN_SERIALIZE_INTERVAL": "0"},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert proc.stderr.strip() == ""  # no traceback
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "hook library missing")
    assert "codex-stop: hook library missing" in content


# ---- codex-hooks.py: shared-library install/uninstall (#108) ----


def test_codex_hooks_install_copies_lib_uninstall_removes_it(tmp_path):
    hooks_dir = tmp_path / ".codex" / "hooks"
    lib = hooks_dir / LIB_NAME

    assert _manager(tmp_path, "install").returncode == 0
    assert lib.exists()  # copied alongside the scripts
    status = _manager(tmp_path, "status")
    assert LIB_NAME in status.stdout
    assert "not installed" not in status.stdout

    assert _manager(tmp_path, "uninstall").returncode == 0
    assert not lib.exists()  # removed once no daimon hook remains
    assert not (hooks_dir / "daimon-codex-session-start.py").exists()
