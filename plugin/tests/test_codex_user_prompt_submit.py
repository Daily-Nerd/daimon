"""Subprocess-level tests for `hook/daimon-codex-user-prompt-submit.py` (#1042).

Codex's `UserPromptSubmit` fires once per user prompt and its plain stdout
reaches the model (measured live on Codex CLI 0.153.1 / daimon 0.46.0, see
the hook's own docstring), exactly like the Claude Code and Kimi prompt
hooks. Codex already gets its briefing from `daimon-codex-session-start.py`
(SessionStart), so this hook carries recall injection only. No first-prompt
briefing branch, unlike Kimi's UserPromptSubmit hook.

These tests mirror `test_claude_hooks.py`'s `daimon-prompt-recall.py` suite
(#125) as closely as the two hooks' shared shape allows.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from daimon_briefing import store

HOOK_DIR = Path(__file__).resolve().parents[2] / "hook"
PROMPT_HOOK = HOOK_DIR / "daimon-codex-user-prompt-submit.py"
VENV_BIN = Path(sys.executable).parent


def _run(payload, tmp_path, extra_env=None, argv=()) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    if extra_env:
        env.update(extra_env)
    stdin = json.dumps(payload) if isinstance(payload, dict) else (payload or "")
    return subprocess.run(
        [sys.executable, str(PROMPT_HOOK), *argv],
        input=stdin, capture_output=True, text=True, env=env, timeout=30,
    )


def _copy_without_lib(script: Path, tmp_path) -> Path:
    """Copy a hook into a lib-less dir, the stale/partial-install shape where
    _daimon_hook_lib.py never landed. The same-dir import then fails."""
    stray = tmp_path / "stray"
    stray.mkdir(exist_ok=True)
    dst = stray / script.name
    dst.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


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
    proc = _run(
        {"cwd": cwd, "session_id": "S-now",
         "prompt": "debugging the litellm gateway cache pinning again"},
        tmp_path,
    )
    assert proc.returncode == 0
    assert "daimon recall:" in proc.stdout
    assert "S-old" in proc.stdout


def test_prompt_hook_silent_on_slash_command(tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(
        {"cwd": cwd, "session_id": "S-now",
         "prompt": "/recall litellm gateway cache pinning"},
        tmp_path,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_silent_on_empty_prompt(tmp_checkpoint_dir, tmp_path):
    proc = _run({"cwd": "/Users/x/projR", "session_id": "S-now", "prompt": "  "},
               tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_silent_when_cli_missing(tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(
        {"cwd": cwd, "session_id": "S-now",
         "prompt": "debugging the litellm gateway cache pinning again"},
        tmp_path, extra_env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_names_the_mcp_tool_when_invoked_with_the_flag(
        tmp_checkpoint_dir, tmp_path):
    # #1036 parity: an argv flag, not an env-var prefix. Codex runs the
    # `command` string through a shell (measured), but the flag form is what
    # every other host uses too, so one contract covers all of them.
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(
        {"cwd": cwd, "session_id": "S-now",
         "prompt": "debugging the litellm gateway cache pinning again"},
        tmp_path, argv=["--mcp-tool"],
    )
    assert proc.returncode == 0
    assert "call the daimon_recall tool with query" in proc.stdout
    assert 'daimon recall "' not in proc.stdout


def test_prompt_hook_keeps_the_shell_form_without_the_flag(
        tmp_checkpoint_dir, tmp_path):
    cwd = "/Users/x/projR"
    _seed_prompt_history(cwd)
    proc = _run(
        {"cwd": cwd, "session_id": "S-now",
         "prompt": "debugging the litellm gateway cache pinning again"},
        tmp_path,
    )
    assert proc.returncode == 0
    assert 'More: daimon recall "' in proc.stdout
    assert "call the daimon_recall tool" not in proc.stdout


def test_prompt_hook_silent_when_lib_missing(tmp_checkpoint_dir, tmp_path):
    stray = _copy_without_lib(PROMPT_HOOK, tmp_path)
    proc = subprocess.run(
        [sys.executable, str(stray)],
        input=json.dumps({"cwd": "/Users/x/projR", "session_id": "S-now",
                          "prompt": "debugging the litellm gateway cache again"}),
        capture_output=True, text=True, timeout=30,
        env={**os.environ,
            "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path)},
    )
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_prompt_hook_delivers_undecided_ask_when_live_delivery_enabled(
        tmp_checkpoint_dir, tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "request-inject" ]; then echo "ASK: undecided thing"; fi\n'
    )
    script.chmod(0o755)
    proc = _run(
        {"cwd": "/Users/x/projR", "session_id": "S-now", "prompt": "hello"},
        tmp_path,
        extra_env={"PATH": str(fake_bin), "DAIMON_LIVE_DELIVERY": "1"},
    )
    assert proc.returncode == 0
    assert "ASK: undecided thing" in proc.stdout


def test_prompt_hook_no_delivery_when_flag_is_off(tmp_checkpoint_dir, tmp_path):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    script = fake_bin / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "request-inject" ]; then echo "ASK: undecided thing"; fi\n'
    )
    script.chmod(0o755)
    proc = _run(
        {"cwd": "/Users/x/projR", "session_id": "S-now", "prompt": "hello"},
        tmp_path,
        extra_env={"PATH": str(fake_bin)},
    )
    assert proc.returncode == 0
    assert "ASK: undecided thing" not in proc.stdout
