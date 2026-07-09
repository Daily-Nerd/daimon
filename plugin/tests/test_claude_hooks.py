"""Subprocess-level tests for the standalone Claude Code hook scripts in hook/.

The scripts are not importable modules (dashed names, standalone); they are
exercised the way Claude Code runs them: payload JSON on stdin, controlled env.
The brief hook shells out to the REAL installed `daimon` CLI from this
repo's venv (the venv bin dir is prepended to PATH), so these are end-to-end:
hook -> CLI -> store, against a tmp DAIMON_CHECKPOINT_DIR.
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
BRIEF_HOOK = HOOK_DIR / "daimon-session-brief.py"
END_HOOK = HOOK_DIR / "daimon-session-end.py"
MANAGER = HOOK_DIR / "daimon-hooks.py"
LIB_NAME = "_daimon_hook_lib.py"
VENV_BIN = Path(sys.executable).parent  # holds the `daimon` console script

# Direct import of the (non-hyphenated) shared lib for unit-level sweep tests
# (#185) — same technique test_windsurf_hooks.py uses for its hyphenated
# sibling: the subprocess-level tests below exercise the real JSON-in/exit-0
# contract, this covers sweep_orphans' internal branches without spawning a
# real child process for every case.
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))
import _daimon_hook_lib as lib  # noqa: E402


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
        "HOME": str(tmp_path),  # keep logs/CLI-fallback out of the real home
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


# ---- daimon-session-brief.py ----


def test_brief_hook_uses_project_checkpoint(tmp_checkpoint_dir, sample_checkpoint, tmp_path):
    cwd = "/Users/x/projA"
    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-mine"
    store.write_checkpoint("S-mine", mine, project_dir=cwd)
    # A later session in ANOTHER project takes over the global latest.
    store.write_checkpoint("S-other", {**sample_checkpoint, "session_id": "S-other"})

    proc = _run(BRIEF_HOOK, {"cwd": cwd, "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-mine" in proc.stdout
    assert "global fallback" not in proc.stdout


def test_brief_hook_age_from_created_not_mtime(tmp_checkpoint_dir, sample_checkpoint, tmp_path):
    # #93: age comes from the written `created` stamp, not file mtime — rotation
    # rewrites prev pointers, moving mtime to rotation time. created ~2h ago while
    # the freshly written latest.json has mtime = now.
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 3600))
    ck = {**sample_checkpoint, "session_id": "S-old", "created": old}
    store.write_checkpoint("S-old", ck, project_dir="/p/A")
    proc = _run(BRIEF_HOOK, {"cwd": "/p/A", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-old" in proc.stdout
    assert "written 2.0h ago" in proc.stdout


def test_brief_hook_labels_global_fallback(tmp_checkpoint_dir, sample_checkpoint, tmp_path):
    # Project is known but has no checkpoint of its own -> global, visibly labeled.
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    proc = _run(BRIEF_HOOK, {"cwd": "/p/never-seen", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-global" in proc.stdout
    assert "global fallback" in proc.stdout


def test_brief_hook_no_cwd_behaves_as_today(tmp_checkpoint_dir, sample_checkpoint, tmp_path):
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    proc = _run(BRIEF_HOOK, {"session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-global" in proc.stdout
    assert "global fallback" not in proc.stdout  # no project known -> nothing to flag


def test_brief_hook_quiet_when_no_checkpoint_anywhere(tmp_checkpoint_dir, tmp_path):
    proc = _run(BRIEF_HOOK, {"cwd": "/p/never-seen"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_brief_hook_hints_install_when_cli_missing(tmp_checkpoint_dir, tmp_path):
    # Plugin-install onboarding (#91): the plugin ships the hooks but not the
    # binary, so the very first session can run with no `daimon` anywhere.
    # The hook must exit 0 with a one-line install hint — not silence (user
    # thinks daimon is broken) and not a stack trace.
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(empty_bin)},
    )
    assert proc.returncode == 0
    assert "uv tool install" in proc.stdout
    assert len(proc.stdout.strip().splitlines()) == 1


def test_brief_hook_fail_open_on_garbage_stdin(tmp_checkpoint_dir, sample_checkpoint, tmp_path):
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    proc = _run(BRIEF_HOOK, "{not json!", tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-global" in proc.stdout  # degraded to global, not dead


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


def test_brief_hook_spawns_heal_detached(tmp_path, tmp_checkpoint_dir):
    # #26: SessionStart opportunistically fires `daimon heal` detached.
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked heal")
    assert "invoked heal" in content


def test_brief_hook_resolves_deprecated_alias(tmp_path, tmp_checkpoint_dir):
    # Deprecated-alias transition: only `daimon-briefing` is on PATH (no `daimon`).
    # The hook must still resolve and spawn it (preferred-then-fallback resolution).
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "invocations.txt"
    script = fake_bin / "daimon-briefing"
    script.write_text(f"#!/bin/sh\nprintf 'invoked %s\\n' \"$1\" >> \"{capture}\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked heal")
    assert "invoked heal" in content


def _fake_team_dir(tmp_path) -> Path:
    """A team dir holding one sidecar-shaped entry (a subdir with .git) — the
    cheap remote-presence marker the hook gates the sync spawn on. No real git
    needed: the hook never runs git itself."""
    team = tmp_path / "team"
    (team / "github-com-org-mem" / ".git").mkdir(parents=True)
    return team


def test_brief_hook_spawns_team_sync_when_remote_present(tmp_path, tmp_checkpoint_dir):
    # #113: SessionStart opportunistically fires `daimon team sync` detached,
    # but ONLY when the team dir holds a real remote clone.
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin),
                   "DAIMON_TEAM_DIR": str(_fake_team_dir(tmp_path))},
    )
    assert proc.returncode == 0
    content = _wait_for_text(capture, "invoked team")
    assert "invoked team" in content
    assert "invoked heal" in content  # heal still fires — sync is additive


def test_brief_hook_no_team_sync_without_remote(tmp_path, tmp_checkpoint_dir):
    # Team feature unused (no sidecar clone) -> no spawn, zero overhead.
    fake_bin, capture = _fake_cli_recording(tmp_path)
    empty_team = tmp_path / "team"
    (empty_team / "local").mkdir(parents=True)  # Phase 1 local mirror only
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin), "DAIMON_TEAM_DIR": str(empty_team)},
    )
    assert proc.returncode == 0
    _wait_for_text(capture, "invoked heal")  # hook ran to completion
    time.sleep(0.3)  # give a (wrongly) spawned sync a chance to record
    assert "invoked team" not in capture.read_text()


def test_brief_hook_disable_suppresses_heal(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli_recording(tmp_path)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA"},
        tmp_path,
        extra_env={"PATH": str(fake_bin), "DAIMON_DISABLE": "1"},
    )
    assert proc.returncode == 0
    time.sleep(0.3)  # give any (wrongly) spawned child a chance to record
    assert not capture.exists()


def _fake_cli_argv_recording(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` that appends each invocation's full argv to a capture
    file (one line per call) and prints a briefing body — lets a test observe
    exactly which flags `_emit_briefing` passed and how many times it retried."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "argv.txt"
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{capture}"\n'
        "echo briefing-body\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def test_brief_hook_passes_auto_flag(tmp_path, tmp_checkpoint_dir, sample_checkpoint):
    # #100: `daimon brief --auto` (Task 1) is only useful if the hook sends it.
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    recorded = capture.read_text()
    assert "--auto" in recorded


def _fake_cli_preflag(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` shaped like a CLI installed before the --auto flag
    shipped: the plugin (and this hook) update independently of the CLI via
    uv/pip, so a newer hook meeting an older CLI on PATH is a real field
    scenario. It rejects --auto with argparse's exit code 2 but briefs fine
    without it. Records each invocation to let a test count the retry."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "argv.txt"
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{capture}"\n'
        'case "$*" in *--auto*) exit 2;; esac\n'
        "echo briefing-body\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def test_brief_hook_retries_without_auto_on_preflag_cli(tmp_path, tmp_checkpoint_dir, sample_checkpoint):
    # #100: exit 2 (argparse rejection) from a pre-flag CLI must degrade to a
    # plain retry, never a lost briefing.
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    fake_bin, capture = _fake_cli_preflag(tmp_path)
    proc = _run(
        BRIEF_HOOK,
        {"cwd": "/Users/x/projA", "session_id": "S-new"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    assert "briefing-body" in proc.stdout  # briefing survived the mismatch

    # Filter to `brief` invocations only: main() also opportunistically spawns
    # `daimon heal` detached against this same fake CLI, and that line must not
    # be mistaken for the retry.
    brief_calls = [
        line for line in capture.read_text().splitlines()
        if line.split()[:1] == ["brief"]
    ]
    assert len(brief_calls) == 2  # --auto attempted first, then the plain retry
    assert "--auto" in brief_calls[0]
    assert "--auto" not in brief_calls[1]


# ---- daimon-session-end.py ----


def _fake_cli(tmp_path) -> tuple[Path, Path]:
    """A fake `daimon` that records its env + args, so the detached
    child spawn can be observed (same capture idiom as the serialize.log smoke)."""
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


def test_session_end_passes_project_dir_to_child(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-end",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "reason": "exit",
    }
    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})
    assert proc.returncode == 0
    captured = _wait_for(capture)
    assert "DAIMON_PROJECT_DIR=/Users/x/projA" in captured
    assert str(transcript) in captured


def _fake_cli_stdout(tmp_path, sentinel: str) -> Path:
    """A fake `daimon` that prints a sentinel result line to stdout, so
    a test can assert the hook does NOT route child stdout into serialize.log."""
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


def test_session_end_does_not_route_child_stdout_into_log(tmp_path, tmp_checkpoint_dir):
    # FR #27: the CLI now logs result lines first-class, so the hook must NOT
    # capture the child's stdout into serialize.log (that double-logged results).
    # serialize.log gets only the hook's own spawn line.
    sentinel = "CHILD-STDOUT-SENTINEL"
    fake_bin = _fake_cli_stdout(tmp_path, sentinel)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-end",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "reason": "exit",
    }
    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})
    assert proc.returncode == 0
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "spawned serialize for S-end")
    # spawn line lands exactly as before; child stdout never reaches the log.
    time.sleep(0.3)  # give the detached child a chance to (not) leak its stdout
    content = log.read_text()
    assert "session-end: spawned serialize for S-end" in content
    assert sentinel not in content


def test_session_end_no_cwd_no_project_env(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    payload = {"session_id": "S-end", "transcript_path": str(transcript), "reason": "exit"}
    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})
    assert proc.returncode == 0
    captured = _wait_for(capture)
    assert "DAIMON_PROJECT_DIR=\n" in captured  # unset for the child, exactly as today


# ---- sweep_orphans (#185): session-start catch-up sweep, unit-level ----


def _age(path: Path, seconds: float) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def test_sweep_orphans_spawns_for_uncaptured_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path / "logs")  # a spawn also logs a breadcrumb
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)  # 1h old, no checkpoint anywhere -> orphan

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    assert calls == [str(orphan)]


def test_sweep_orphans_spawns_only_the_newest_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path / "logs")  # a spawn also logs a breadcrumb
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    older = transcripts / "S-old.jsonl"
    older.write_text("{}\n")
    _age(older, 7200)
    newer = transcripts / "S-newer.jsonl"
    newer.write_text("{}\n")
    _age(newer, 60)

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    assert calls == [str(newer)]


def test_sweep_orphans_excludes_current_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    _age(current, 3600)  # old + no checkpoint, but IS the current session

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    assert calls == []


def test_sweep_orphans_skips_when_checkpoint_is_newer_than_transcript(tmp_path, monkeypatch):
    ckpt_dir = tmp_path / "checkpoints"
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(ckpt_dir))
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    captured = transcripts / "S-captured.jsonl"
    captured.write_text("{}\n")
    _age(captured, 3600)  # 1h old transcript
    ckpt_dir.mkdir(parents=True)
    (ckpt_dir / "S-captured.json").write_text("{}\n")  # checkpoint written NOW -> newer

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    assert calls == []


def test_sweep_orphans_ignores_transcripts_older_than_14_days(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    stale = transcripts / "S-ancient.jsonl"
    stale.write_text("{}\n")
    _age(stale, 15 * 24 * 3600)  # 15 days old, no checkpoint -> still ignored

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    assert calls == []


def test_sweep_orphans_logs_breadcrumb_on_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    # _daimon_hook_lib.LOG_DIR is a module-level constant (Path.home()-derived,
    # not env-driven — these are standalone scripts, no DAIMON_LOG_DIR seam),
    # so it's monkeypatched directly rather than via env var.
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path / "logs")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)

    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: None)
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))
    log_text = (tmp_path / "logs" / "serialize.log").read_text(encoding="utf-8")
    assert "session-start: catch-up serialize spawned for S-orphan (orphaned transcript)" in log_text


def test_sweep_orphans_noop_without_cli(tmp_path, monkeypatch):
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)

    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans(None, "/p/A", "S-new", str(current))
    assert calls == []


def test_sweep_orphans_noop_without_transcript_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", "")
    assert calls == []


def test_sweep_orphans_never_raises_when_spawn_serialize_explodes(tmp_path, monkeypatch):
    # Fail-open (#185): a sweep error must never propagate into the hook —
    # the briefing has already printed by the time this runs.
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path / "logs")
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)

    def _boom(*_a, **_k):
        raise OSError("spawn failed")

    monkeypatch.setattr(lib, "spawn_serialize", _boom)
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(current))  # must not raise
    log_text = (tmp_path / "logs" / "serialize.log").read_text(encoding="utf-8")
    assert "session-start: catch-up sweep failed" in log_text


def test_sweep_orphans_never_raises_when_directory_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    calls = []
    monkeypatch.setattr(lib, "spawn_serialize", lambda cli, path, env: calls.append(path))
    lib.sweep_orphans("daimon", "/p/A", "S-new", str(tmp_path / "gone" / "S-new.jsonl"))
    assert calls == []


# ---- daimon-session-brief.py: orphan sweep wired end-to-end (#185) ----


def test_brief_hook_spawns_catch_up_serialize_for_orphaned_transcript(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")
    orphan = transcripts / "S-orphan.jsonl"
    orphan.write_text("{}\n")
    _age(orphan, 3600)

    proc = _run(
        BRIEF_HOOK,
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


def test_brief_hook_no_catch_up_spawn_without_orphan(tmp_path, tmp_checkpoint_dir):
    fake_bin, capture = _fake_cli_argv_recording(tmp_path)
    transcripts = tmp_path / "proj"
    transcripts.mkdir()
    current = transcripts / "S-new.jsonl"
    current.write_text("{}\n")

    proc = _run(
        BRIEF_HOOK,
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


def test_brief_hook_sweep_never_breaks_briefing_output(
    tmp_path, tmp_checkpoint_dir, sample_checkpoint
):
    # No transcript_path in the payload at all -> sweep can't act, briefing is
    # unaffected (matches every pre-#185 brief-hook test's payload shape).
    store.write_checkpoint("S-global", {**sample_checkpoint, "session_id": "S-global"})
    proc = _run(BRIEF_HOOK, {"cwd": "/p/never-seen", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "checkpoint: S-global" in proc.stdout


# ---- lib-missing fail-open (#108) ----


def test_brief_hook_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    # Stale/partial install: the hook is present but _daimon_hook_lib.py is not.
    # It must fail open (exit 0) with a one-line diagnostic, never a traceback.
    script = _copy_without_lib(BRIEF_HOOK, tmp_path)
    proc = _run(script, {"cwd": "/Users/x/projA", "session_id": "S-new"}, tmp_path)
    assert proc.returncode == 0
    assert "hook library missing" in proc.stdout
    assert proc.stderr.strip() == ""  # no traceback
    assert len(proc.stdout.strip().splitlines()) == 1


def test_session_end_fail_open_when_lib_missing(tmp_path, tmp_checkpoint_dir):
    script = _copy_without_lib(END_HOOK, tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-end",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "reason": "exit",
    }
    proc = _run(script, payload, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # SessionEnd never prints
    assert proc.stderr.strip() == ""  # no traceback
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "hook library missing")
    assert "session-end: hook library missing" in content


# ---- daimon-hooks.py: shared-library install/uninstall (#108) ----


def _seed_claude_settings(tmp_path) -> None:
    # Seed an empty settings.json to exercise the common in-the-wild state
    # (Claude Code usually ships one). The no-settings fresh-install path is
    # covered separately by test_claude_hooks_fresh_install_without_settings_json.
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    (claude / "settings.json").write_text("{}\n")


def test_claude_hooks_fresh_install_without_settings_json(tmp_path):
    # #109: a fresh machine has no ~/.claude/settings.json; save_settings used to
    # back it up unconditionally and crash. Install must succeed and write it.
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    proc = _manager(tmp_path, "install")
    assert proc.returncode == 0, proc.stderr
    settings = tmp_path / ".claude" / "settings.json"
    assert settings.exists()
    assert "hooks" in json.loads(settings.read_text(encoding="utf-8"))


def test_claude_hooks_install_copies_lib_uninstall_removes_it(tmp_path):
    _seed_claude_settings(tmp_path)
    hooks_dir = tmp_path / ".claude" / "hooks"
    lib = hooks_dir / LIB_NAME

    assert _manager(tmp_path, "install").returncode == 0
    assert lib.exists()  # copied alongside the scripts
    status = _manager(tmp_path, "status")
    assert LIB_NAME in status.stdout
    assert "not installed" not in status.stdout

    assert _manager(tmp_path, "uninstall").returncode == 0
    assert not lib.exists()  # removed once no daimon hook remains
    assert not (hooks_dir / "daimon-session-brief.py").exists()


def test_claude_hooks_uninstall_keeps_lib_when_foreign_daimon_hook_present(tmp_path):
    _seed_claude_settings(tmp_path)
    hooks_dir = tmp_path / ".claude" / "hooks"
    assert _manager(tmp_path, "install").returncode == 0
    # Another tool's daimon-* hook lives in the same dir and still needs the lib.
    (hooks_dir / "daimon-other-tool.py").write_text("# not ours\n")
    assert _manager(tmp_path, "uninstall").returncode == 0
    assert (hooks_dir / LIB_NAME).exists()  # kept: a foreign daimon hook remains


# ---- daimon-prompt-recall.py (#125) ----

PROMPT_HOOK = HOOK_DIR / "daimon-prompt-recall.py"


def _seed_prompt_history(cwd):
    store.write_checkpoint(
        "S-old",
        {"session_id": "S-old", "created": "2026-06-20T00:00:00Z",
         "working_context": {
             "active_topic": {"text": "gateway debugging", "trust": "inferred"},
             "open_questions": [{
                 "text": "LiteLLM gateway response cache pins identical bad responses",
                 "trust": "verbatim", "quote": "cache answers instantly",
                 "importance": 9, "first_seen": "2026-06-20T00:00:00Z"}],
             "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}},
        project_dir=cwd,
    )
    store.write_checkpoint(
        "S-latest",
        {"session_id": "S-latest", "created": "2026-06-28T00:00:00Z",
         "working_context": {
             "active_topic": {"text": "unrelated newer work", "trust": "inferred"},
             "open_questions": [], "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}},
        project_dir=cwd,
    )


def test_prompt_hook_injects_prior_work(tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(PROMPT_HOOK,
                {"cwd": cwd, "session_id": "S-now",
                 "prompt": "debugging the litellm gateway cache pinning again"},
                tmp_path)
    assert proc.returncode == 0
    assert "daimon recall:" in proc.stdout
    assert "S-old" in proc.stdout


def test_prompt_hook_silent_on_slash_command(tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(PROMPT_HOOK,
                {"cwd": cwd, "session_id": "S-now",
                 "prompt": "/recall litellm gateway cache pinning"},
                tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_silent_on_empty_prompt(tmp_checkpoint_dir, tmp_path):
    proc = _run(PROMPT_HOOK, {"cwd": "/Users/x/projR", "session_id": "S-now",
                              "prompt": "  "}, tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_silent_when_cli_missing(tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(PROMPT_HOOK,
                {"cwd": cwd, "session_id": "S-now",
                 "prompt": "debugging the litellm gateway cache pinning again"},
                tmp_path, extra_env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_silent_when_lib_missing(tmp_checkpoint_dir, tmp_path):
    stray = _copy_without_lib(PROMPT_HOOK, tmp_path)
    proc = _run(stray, {"cwd": "/Users/x/projR", "session_id": "S-now",
                        "prompt": "debugging the litellm gateway cache again"},
                tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_session_end_spawn_line_records_transcript(tmp_path, tmp_checkpoint_dir):
    # #28: the spawn line must carry the transcript path — a child that
    # crashes before writing a result line leaves no other pointer to it,
    # and heal needs it to classify the hung session as healable.
    fake_bin, _capture = _fake_cli(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    payload = {
        "session_id": "S-tr",
        "transcript_path": str(transcript),
        "cwd": "/Users/x/projA",
        "reason": "exit",
    }
    proc = _run(END_HOOK, payload, tmp_path, extra_env={"PATH": str(fake_bin)})
    assert proc.returncode == 0
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = _wait_for_text(log, "spawned serialize for S-tr")
    assert f"(transcript: {transcript})" in content


def test_windsurf_probe_captures_payload_and_transcript_sample(tmp_path):
    # #35: the probe must dump the raw payload, parsed JSON, and a head sample
    # of any on-disk *path* field — and always exit 0.
    import subprocess
    probe = Path(__file__).resolve().parents[2] / "hook" / "daimon-windsurf-probe.py"
    transcript = tmp_path / "trajectory.jsonl"
    transcript.write_text('{"type":"user_prompt","status":"done"}\n' * 3)
    payload = {"trajectory_id": "T1", "transcript_path": str(transcript)}
    proc = subprocess.run(
        [sys.executable, str(probe)], input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    out_dir = tmp_path / "daimon-windsurf-probe"
    files = {p.name.split("-", 1)[0] + Path(p.name).suffix for p in out_dir.iterdir()}
    assert any(f.startswith("payload") and f.endswith(".json") for f in files)
    samples = list(out_dir.glob("sample-transcript_path-*.txt"))
    assert samples and "user_prompt" in samples[0].read_text()


def test_windsurf_probe_non_json_stdin_still_exits_zero(tmp_path):
    import subprocess
    probe = Path(__file__).resolve().parents[2] / "hook" / "daimon-windsurf-probe.py"
    proc = subprocess.run(
        [sys.executable, str(probe)], input="not json at all",
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    assert list((tmp_path / "daimon-windsurf-probe").glob("payload-*.raw"))


def test_windsurf_probe_scan_vscdb_finds_trajectory_key(tmp_path):
    # #35: Windsurf stores Cascade state in VS Code-style sqlite (state.vscdb,
    # ItemTable). The scan mode must report which key holds a given trajectory
    # WITHOUT shipping whole conversation blobs — key names, sizes, and a
    # small head of the matching value only.
    import sqlite3
    probe = Path(__file__).resolve().parents[2] / "hook" / "daimon-windsurf-probe.py"
    db = tmp_path / "state.vscdb"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    traj = "b0ba5494-dce6-47a2-8da0-a7c11b18d392"
    blob = json.dumps({"trajectories": [{"id": traj, "turns": [
        {"role": "user", "text": "hola"},
        {"role": "assistant", "text": "### Planner Response\n\nHola!"}]}]})
    conn.execute("INSERT INTO ItemTable VALUES (?, ?)",
                 ("windsurf.cascadeState", blob.encode()))
    conn.execute("INSERT INTO ItemTable VALUES (?, ?)",
                 ("editor.fontSize", b"14"))
    conn.commit()
    conn.close()

    proc = subprocess.run(
        [sys.executable, str(probe), "--scan-vscdb", traj, "--db", str(db)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    reports = list((tmp_path / "daimon-windsurf-probe").glob("vscdb-scan-*.txt"))
    assert reports, "scan report not written"
    report = reports[0].read_text()
    assert "windsurf.cascadeState" in report          # the key that matched
    assert traj in report
    assert "hola" in report                            # head sample present
    assert "editor.fontSize" not in report or "MATCH" not in report.split("editor.fontSize")[1][:40]


def test_windsurf_probe_scan_vscdb_missing_db_exits_zero(tmp_path):
    import subprocess
    probe = Path(__file__).resolve().parents[2] / "hook" / "daimon-windsurf-probe.py"
    proc = subprocess.run(
        [sys.executable, str(probe), "--scan-vscdb", "whatever",
         "--db", str(tmp_path / "absent.vscdb")],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0


def test_windsurf_probe_hunt_finds_id_in_arbitrary_files(tmp_path):
    # #35: state.vscdb turned out to hold UI state only — the hunt walks
    # bounded roots and reports every file containing the trajectory id
    # (paths + sizes + small context head, never whole files).
    import subprocess
    probe = Path(__file__).resolve().parents[2] / "hook" / "daimon-windsurf-probe.py"
    traj = "b0ba5494-dce6-47a2-8da0-a7c11b18d392"
    root = tmp_path / "store"
    (root / "deep" / "nested").mkdir(parents=True)
    hit = root / "deep" / "nested" / "conv.leveldb-log"
    hit.write_bytes(b"\x00binary\x01" + f'{{"trajectory_id":"{traj}","turns":["hola"]}}'.encode() + b"\x02")
    (root / "noise.txt").write_text("nothing here")
    proc = subprocess.run(
        [sys.executable, str(probe), "--hunt", traj, "--root", str(root)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0
    reports = list((tmp_path / "daimon-windsurf-probe").glob("hunt-*.txt"))
    assert reports, "hunt report not written"
    report = reports[0].read_text()
    assert "conv.leveldb-log" in report
    assert "hola" in report          # context head around the match
    assert "noise.txt" not in report
