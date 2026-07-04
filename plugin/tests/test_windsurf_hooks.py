"""Subprocess-level tests for the Windsurf Cascade adapter (#35).

Ground truth (probe rounds 1-2, real machine): `post_cascade_response`
delivers {agent_action_name, trajectory_id, execution_id, timestamp,
model_name, tool_info.response} — no transcript_path; state.vscdb holds UI
state only. The adapter therefore ACCUMULATES its own transcript per
trajectory and serializes it on a throttle.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

HOOK_DIR = Path(__file__).parents[2] / "hook"
HOOK = HOOK_DIR / "daimon-windsurf-hooks.py"
VENV_BIN = Path(sys.executable).parent

TRAJ = "b0ba5494-dce6-47a2-8da0-a7c11b18d392"


def _post_payload(response="### Planner Response\n\nDone."):
    return {
        "agent_action_name": "post_cascade_response",
        "trajectory_id": TRAJ,
        "timestamp": "2026-07-03T16:45:48.476887-06:00",
        "execution_id": "exec-1",
        "model_name": "Claude Sonnet 4.5",
        "tool_info": {"response": response},
    }


def _fake_cli(tmp_path):
    """A fake `daimon` on PATH that records its argv."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    capture = tmp_path / "cli-calls.txt"
    script = fake_bin / "daimon"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> {capture}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return fake_bin, capture


def _run(payload, tmp_path, extra_env=None, cwd=None):
    fake_bin, capture = _fake_cli(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
    }
    if extra_env:
        env.update(extra_env)
    stdin = json.dumps(payload) if isinstance(payload, dict) else (payload or "")
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True,
        text=True, env=env, timeout=30, cwd=cwd or str(tmp_path),
    )
    return proc, capture


def _transcript(tmp_path):
    return tmp_path / ".daimon" / "windsurf" / "transcripts" / f"{TRAJ}.md"


def test_post_response_appends_assistant_turn(tmp_path):
    proc, _ = _run(_post_payload("Hola, aquí va la respuesta."), tmp_path)
    assert proc.returncode == 0
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert "**assistant**:" in text
    assert "Hola, aquí va la respuesta." in text


def test_pre_user_prompt_appends_user_turn(tmp_path):
    payload = {"agent_action_name": "pre_user_prompt",
               "trajectory_id": TRAJ,
               "tool_info": {"prompt": "arreglá el bug de auth"}}
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert "**user**:" in text
    assert "arreglá el bug de auth" in text


def test_turns_accumulate_in_order(tmp_path):
    _run({"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
          "tool_info": {"prompt": "pregunta uno"}}, tmp_path)
    _run(_post_payload("respuesta uno"), tmp_path)
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert text.index("pregunta uno") < text.index("respuesta uno")


def test_unknown_pre_prompt_shape_dumps_debug_and_exits_zero(tmp_path):
    # Self-probing (#35): a payload the adapter can't extract text from must
    # not be lost — it lands in a debug dump for the next adapter iteration.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"weird_field": {"nested": True}}}
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert dumps and "weird_field" in dumps[0].read_text()


def _wait_for_capture(capture: Path, timeout=10.0) -> str:
    """The serialize child is detached — poll for its argv capture."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if capture.exists() and capture.read_text().strip():
            return capture.read_text()
        time.sleep(0.05)
    raise AssertionError(f"fake daimon was never invoked ({capture})")


def test_post_response_spawns_throttled_serialize(tmp_path):
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600"}
    proc, capture = _run(_post_payload("first"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    calls = _wait_for_capture(capture)
    assert "serialize" in calls
    assert TRAJ in calls
    # second post inside the interval: accumulates but does NOT spawn again.
    # (The throttle decision is synchronous — the hook has exited by the time
    # _run returns, so a short settle covers only a straggler child echo.)
    proc, capture2 = _run(_post_payload("second"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    time.sleep(0.5)
    assert capture2.read_text().count("serialize") == 1
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert "second" in text  # accumulation never throttles


def test_spawn_line_carries_host_prefix_and_transcript(tmp_path):
    proc, _ = _run(_post_payload(), tmp_path,
                   extra_env={"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "0"})
    assert proc.returncode == 0
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    content = log.read_text(encoding="utf-8")
    assert f"windsurf-cascade: spawned serialize for {TRAJ}" in content
    assert "(transcript:" in content


def test_disabled_kill_switch_is_silent(tmp_path):
    proc, capture = _run(_post_payload(), tmp_path,
                         extra_env={"DAIMON_DISABLE": "1"})
    assert proc.returncode == 0
    assert not _transcript(tmp_path).exists()
    assert not capture.exists() or "serialize" not in capture.read_text()


def test_unparseable_stdin_exits_zero(tmp_path):
    proc, _ = _run("not json", tmp_path)
    assert proc.returncode == 0


def test_missing_trajectory_id_dumps_probe_payload(tmp_path):
    # #62: a payload without trajectory_id must still land in a probe dump —
    # the field showed this path swallowing pre_user_prompt invisibly.
    proc, _ = _run({"agent_action_name": "pre_user_prompt",
                    "tool_info": {"user_prompt": "hola"}}, tmp_path)
    assert proc.returncode == 0
    assert not (tmp_path / ".daimon" / "windsurf" / "transcripts").exists()
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert dumps and "hola" in dumps[0].read_text(encoding="utf-8")


def test_unhandled_event_dumps_once(tmp_path):
    # #62: unknown agent_action_name must probe-dump instead of vanishing —
    # and repeated payloads for the same event must not flood the state dir.
    payload = {"agent_action_name": "post_cascade_response_with_transcript",
               "trajectory_id": TRAJ,
               "tool_info": {"transcript_path": "/x/y.jsonl"}}
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert len(dumps) == 1
    assert "transcript_path" in dumps[0].read_text(encoding="utf-8")


def test_unparsed_pre_prompt_dump_bounded_once(tmp_path):
    # #62: the existing pre_user_prompt shape dump joins the same
    # one-dump-per-event bound.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"weird_field": {"nested": True}}}
    _run(payload, tmp_path)
    _run(payload, tmp_path)
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert len(dumps) == 1


def test_pre_user_prompt_docs_shape_appends_user_turn(tmp_path):
    # Documented shape (docs.devin.ai/desktop/cascade/hooks): text lives in
    # tool_info.user_prompt. Regression guard — passes on the current
    # extractor by design.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"user_prompt": "can you run the echo hello command"}}
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert "**user**:" in text
    assert "can you run the echo hello command" in text
