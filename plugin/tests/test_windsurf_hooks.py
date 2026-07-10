"""Subprocess-level tests for the Windsurf Cascade adapter (#35).

Ground truth (probe rounds 1-2, real machine): `post_cascade_response`
delivers {agent_action_name, trajectory_id, execution_id, timestamp,
model_name, tool_info.response} — no transcript_path; state.vscdb holds UI
state only. The adapter therefore ACCUMULATES its own transcript per
trajectory and serializes it on a throttle.
"""

import importlib.util
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


def _load_hook_module():
    """Import the hyphenated hook script as a module so its internal helpers
    (e.g. _redact_payload) can be unit-tested directly — the subprocess path
    only exercises JSON-shaped payloads, which can never carry tuples or
    unknown-type objects."""
    spec = importlib.util.spec_from_file_location("daimon_windsurf_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

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


def _with_transcript_payload(transcript_path):
    return {
        "agent_action_name": "post_cascade_response_with_transcript",
        "trajectory_id": TRAJ,
        "timestamp": "2026-07-03T16:45:48.476887-06:00",
        "execution_id": "exec-1",
        "model_name": "Claude Sonnet 4.5",
        "tool_info": {"transcript_path": str(transcript_path)},
    }


def test_with_transcript_existing_path_spawns_native_serialize(tmp_path):
    # #70: the native Cascade transcript, when present, is what gets
    # serialized — NOT the daimon-side accumulation file, and no probe dump.
    native = tmp_path / "native-transcript.jsonl"
    native.write_text('{"type": "user_input", "status": "done", '
                      '"user_input": {"user_response": "hola"}}\n',
                      encoding="utf-8")
    payload = _with_transcript_payload(native)
    proc, capture = _run(payload, tmp_path,
                         extra_env={"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "0"})
    assert proc.returncode == 0
    calls = _wait_for_capture(capture)
    assert str(native) in calls
    assert not _transcript(tmp_path).exists()
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert dumps == []


def test_with_transcript_missing_path_dumps_probe_no_spawn(tmp_path):
    missing = tmp_path / "does-not-exist.jsonl"
    payload = _with_transcript_payload(missing)
    proc, capture = _run(payload, tmp_path,
                         extra_env={"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "0"})
    assert proc.returncode == 0
    time.sleep(0.3)
    assert not capture.exists() or "serialize" not in capture.read_text()
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert dumps and str(missing) in dumps[0].read_text(encoding="utf-8")


def test_with_transcript_shares_throttle_marker_with_accumulation(tmp_path):
    # Both post events share the SAME per-trajectory marker — registering
    # both must never double-spawn inside one throttle interval.
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600"}
    proc, capture = _run(_post_payload("first"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    _wait_for_capture(capture)

    native = tmp_path / "native-transcript.jsonl"
    native.write_text('{"type": "planner_response", "status": "done", '
                      '"planner_response": {"response": "listo"}}\n',
                      encoding="utf-8")
    payload = _with_transcript_payload(native)
    proc, capture2 = _run(payload, tmp_path, extra_env=extra)
    assert proc.returncode == 0
    time.sleep(0.5)
    assert capture2.read_text().count("serialize") == 1  # unchanged: no new spawn


_STRIPE = "sk_live_a1B2c3D4e5F6g7H8"


def test_post_response_redacts_secret_in_accumulated_transcript(tmp_path):
    # #109: the accumulation .md is a disk artifact `daimon serialize` reads
    # later — a quoted secret in a turn must be scrubbed at the append write
    # site so it never reaches disk raw (mirrors the #104 checkpoint seam).
    proc, _ = _run(_post_payload(f"deploy with {_STRIPE} now"), tmp_path)
    assert proc.returncode == 0
    text = _transcript(tmp_path).read_text(encoding="utf-8")
    assert _STRIPE not in text
    assert "[redacted:stripe-key]" in text


def test_probe_dump_redacts_secret_but_keeps_structure(tmp_path):
    # #109: probe dumps persist raw event payloads. Secret-shaped values must
    # be scrubbed while keys / structure survive, so the dump stays a usable
    # diagnostic for the next adapter iteration.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"weird_field": {"note": f"key is {_STRIPE}"}}}
    proc, _ = _run(payload, tmp_path)
    assert proc.returncode == 0
    dumps = list((tmp_path / ".daimon" / "windsurf").glob("unparsed-*.json"))
    assert dumps
    body = dumps[0].read_text(encoding="utf-8")
    assert _STRIPE not in body
    assert "[redacted:stripe-key]" in body
    parsed = json.loads(body)  # still valid JSON, structure intact
    assert parsed["tool_info"]["weird_field"]["note"]  # key + leaf preserved


def test_redact_payload_handles_tuple_and_unknown_type():
    # #109 hardening: dict/list/str is not the whole space. A tuple's strings
    # and any unrecognized container/object must NOT slip through unredacted —
    # a silent skip contradicts the disk-wide guarantee if the helper is reused.
    mod = _load_hook_module()

    class _Weird:
        def __str__(self):
            return "leaked sk_live_a1B2c3D4e5F6g7H8 here"

    out = mod._redact_payload(("plain", _STRIPE, _Weird()))
    assert isinstance(out, list)  # tuple normalized to a json-dumpable list
    assert out[0] == "plain"
    assert _STRIPE not in out[1] and "[redacted:stripe-key]" in out[1]
    # unknown type: fail-safe str() rendering, scrubbed — never passed through raw
    assert isinstance(out[2], str)
    assert _STRIPE not in out[2] and "[redacted:stripe-key]" in out[2]


def test_redact_payload_preserves_scalars():
    # Non-string scalars are structural, not secret carriers — they pass through
    # untouched so the dump stays faithful.
    mod = _load_hook_module()
    assert mod._redact_payload({"n": 42, "f": 1.5, "b": True, "z": None}) == {
        "n": 42, "f": 1.5, "b": True, "z": None}


def test_redaction_unavailable_skips_and_persists_no_raw_text(tmp_path):
    # #141 (pins the #109 skip branch): with redact.py missing at hook runtime
    # — a stale install — the hook must skip instead of writing raw: exit 0,
    # a skip line in serialize.log, and NO transcript-derived text on disk.
    bare = tmp_path / "barehooks"
    bare.mkdir()
    for name in ("daimon-windsurf-hooks.py", "_daimon_hook_lib.py"):
        (bare / name).write_bytes((HOOK_DIR / name).read_bytes())
    fake_bin, capture = _fake_cli(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "0",
    }
    payload = _post_payload(f"deploy with {_STRIPE} now")
    proc = subprocess.run(
        [sys.executable, str(bare / "daimon-windsurf-hooks.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=30, cwd=str(tmp_path),
    )
    assert proc.returncode == 0  # fail-open: skip, never break Cascade
    log = (tmp_path / ".daimon" / "logs" / "serialize.log").read_text(encoding="utf-8")
    assert "redaction module unavailable" in log and "skipped" in log
    assert not _transcript(tmp_path).exists()
    assert not capture.exists() or "serialize" not in capture.read_text()
    # the secret from the payload reached NO file under this HOME
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert _STRIPE not in p.read_text(encoding="utf-8", errors="replace")


# ---- #42: debounced finalizer — flush the session tail after a quiet period ----
#
# Windsurf has no session-end event; a session whose last turns land inside the
# serialize throttle window would otherwise never be serialized again. Every
# post event arms a detached one-shot sleeper; the LAST turn's sleeper (the only
# one whose activity stamp is still unchanged at wake) fires the final serialize.


def _activity_stamp(tmp_path):
    return tmp_path / ".daimon" / "windsurf" / f"{TRAJ}.last-activity"


def _preload_throttle_marker(tmp_path):
    """Pre-date the per-trajectory throttle marker so the IMMEDIATE spawn path
    is throttled — any serialize the fake CLI then records came from the
    finalizer, not from the turn itself."""
    state = tmp_path / ".daimon" / "windsurf"
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{TRAJ}.last-serialize").write_text(str(int(time.time())), encoding="utf-8")


def _wait_for_log(tmp_path, needle, timeout=10.0) -> str:
    """Poll serialize.log for a line — finalizer children are detached."""
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists():
            text = log.read_text(encoding="utf-8")
            if needle in text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"{needle!r} never appeared in {log}")


def test_finalizer_fires_after_quiet_period_with_accumulation_path(tmp_path):
    # The last turn of a session lands inside the throttle window (immediate
    # spawn skipped); after the quiet period the finalizer must serialize the
    # ACCUMULATED .md transcript and leave a ledger-shaped spawn line.
    _preload_throttle_marker(tmp_path)
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600",
             "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.2"}
    proc, capture = _run(_post_payload("tail turn"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    assert _activity_stamp(tmp_path).exists()  # armed
    calls = _wait_for_capture(capture)
    assert "serialize" in calls
    assert str(_transcript(tmp_path)) in calls
    log = _wait_for_log(tmp_path, f"windsurf-finalizer: spawned serialize for {TRAJ}")
    assert "(transcript:" in log


def test_finalizer_with_transcript_event_arms_native_path(tmp_path):
    # A with_transcript tail turn must arm the finalizer with the NATIVE
    # .jsonl path — the source of truth for that trajectory.
    _preload_throttle_marker(tmp_path)
    native = tmp_path / "native-transcript.jsonl"
    native.write_text('{"type": "user_input", "status": "done", '
                      '"user_input": {"user_response": "hola"}}\n',
                      encoding="utf-8")
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600",
             "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.2"}
    proc, capture = _run(_with_transcript_payload(native), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    calls = _wait_for_capture(capture)
    assert str(native) in calls
    _wait_for_log(tmp_path, f"windsurf-finalizer: spawned serialize for {TRAJ}")


def test_finalizer_stays_quiet_when_a_later_turn_refreshes_activity(tmp_path):
    # Last-writer-wins: refreshing the activity stamp after arming means a
    # later turn owns finality — the armed sleeper must wake and exit silently.
    _preload_throttle_marker(tmp_path)
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600",
             "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.6"}
    proc, capture = _run(_post_payload("not the tail"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    stamp = _activity_stamp(tmp_path)
    assert stamp.exists()
    os.utime(stamp)  # simulate a later turn touching the stamp
    time.sleep(1.2)  # quiet period elapsed with margin
    assert not capture.exists() or "serialize" not in capture.read_text()
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    assert "windsurf-finalizer:" not in (
        log.read_text(encoding="utf-8") if log.exists() else "")


def test_finalizer_quiet_zero_disables_arming(tmp_path):
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "0",
             "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0"}
    proc, capture = _run(_post_payload(), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    _wait_for_capture(capture)  # the immediate (unthrottled) spawn still runs
    assert not _activity_stamp(tmp_path).exists()
    time.sleep(0.4)
    assert capture.read_text().count("serialize") == 1
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    assert "windsurf-finalizer:" not in (
        log.read_text(encoding="utf-8") if log.exists() else "")


def test_finalizer_respects_kill_switch(tmp_path):
    proc, capture = _run(
        _post_payload(), tmp_path,
        extra_env={"DAIMON_DISABLE": "1",
                   "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.1"})
    assert proc.returncode == 0
    assert not _activity_stamp(tmp_path).exists()
    time.sleep(0.4)
    assert not capture.exists() or "serialize" not in capture.read_text()


def test_pre_user_prompt_does_not_arm_finalizer(tmp_path):
    # A user prompt is trajectory ACTIVITY (it must touch the stamp) but not a
    # candidate session tail (it must NOT arm a sleeper): if the session dies
    # between prompt and response, the lone unanswered prompt isn't worth a
    # finalizer — the response's post event arms as usual.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"prompt": "hola"}}
    proc, capture = _run(payload, tmp_path,
                         extra_env={"DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.1"})
    assert proc.returncode == 0
    assert _activity_stamp(tmp_path).exists()  # touched: a prompt is activity
    time.sleep(0.4)  # quiet elapsed, stamp unchanged — an armed sleeper WOULD fire
    assert not capture.exists() or "serialize" not in capture.read_text()
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    assert "windsurf-finalizer:" not in (
        log.read_text(encoding="utf-8") if log.exists() else "")


def test_pre_user_prompt_quiet_zero_touches_nothing(tmp_path):
    # With the finalizer disabled, the prompt-side touch stays inert too — no
    # stamp file appears.
    payload = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
               "tool_info": {"prompt": "hola"}}
    proc, _ = _run(payload, tmp_path,
                   extra_env={"DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0"})
    assert proc.returncode == 0
    assert not _activity_stamp(tmp_path).exists()


def test_pre_user_prompt_defuses_previously_armed_sleeper(tmp_path):
    # A prompt arriving inside the quiet window means the assistant is still
    # generating a response — the sleeper armed by the PREVIOUS post event
    # must see the refreshed stamp at wake and exit without serializing a
    # mid-generation transcript state.
    _preload_throttle_marker(tmp_path)
    extra = {"DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL": "3600",
             "DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS": "0.6"}
    proc, capture = _run(_post_payload("previous response"), tmp_path, extra_env=extra)
    assert proc.returncode == 0
    assert _activity_stamp(tmp_path).exists()  # armed
    prompt = {"agent_action_name": "pre_user_prompt", "trajectory_id": TRAJ,
              "tool_info": {"prompt": "follow-up question"}}
    proc, _ = _run(prompt, tmp_path, extra_env=extra)  # inside the quiet window
    assert proc.returncode == 0
    time.sleep(1.2)  # quiet period elapsed with margin
    assert not capture.exists() or "serialize" not in capture.read_text()
    log = tmp_path / ".daimon" / "logs" / "serialize.log"
    assert "windsurf-finalizer:" not in (
        log.read_text(encoding="utf-8") if log.exists() else "")


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
