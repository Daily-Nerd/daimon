"""Subprocess-level tests for the standalone Kimi Code hook scripts (#988).

Kimi differs from every host daimon already adapts in one way that shapes all
three scripts: the hook payload carries a `session_id` and NO transcript path,
and no environment variable carries one either. The path has to be resolved
from the id against the host's own session storage, so "transcript not found"
is a first-class outcome here rather than a corrupted-payload edge case, and
each script has to say so in the log instead of failing silently.

The other shaping fact: `kimi -p` print mode never fires `SessionEnd`
(measured twice), so `Stop` is the only capture path for it. That makes the
throttled Stop hook load-bearing rather than pure crash insurance, and it
makes double-capture a real risk worth a test.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parents[2] / "hook"
PROMPT_HOOK = HOOK_DIR / "daimon-kimi-user-prompt-submit.py"
SESSION_END_HOOK = HOOK_DIR / "daimon-kimi-session-end.py"
STOP_HOOK = HOOK_DIR / "daimon-kimi-stop.py"
VENV_BIN = Path(sys.executable).parent

SESSION = "session_8a1593d9-376b-43d3-abc9-5796eb848fa3"


def _run(script: Path, payload, home, extra_env=None):
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
    }
    env.pop("KIMI_CODE_HOME", None)
    if extra_env:
        env.update(extra_env)
    stdin = json.dumps(payload) if isinstance(payload, dict) else (payload or "")
    return subprocess.run([sys.executable, str(script)], input=stdin,
                          capture_output=True, text=True, env=env, timeout=30)


def _wire(home: Path, session=SESSION, workspace="wd_proj_0123456789ab") -> Path:
    """A minimal but real wire.jsonl at the measured storage path."""
    path = (home / ".kimi-code" / "sessions" / workspace / session
            / "agents" / "main" / "wire.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "metadata", "protocol_version": "1.5",
                    "created_at": 1788971099783}) + "\n",
        encoding="utf-8")
    return path


def _fake_cli(home: Path):
    """A `daimon` on PATH that appends its argv to a file and exits 0."""
    bin_dir = home / "fakebin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    capture = home / "invocations.txt"
    script = bin_dir / "daimon"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{capture}"\n'
        "exit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return bin_dir, capture


def _wait_for(path: Path, needle="", timeout=10.0) -> str:
    """The serialize spawn is DETACHED, so the hook returns before the child
    has run. Poll rather than sleep a fixed slice."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if needle in text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"never saw {needle!r} in {path}")


def _spawns(path: Path) -> int:
    """How many times the fake CLI was invoked.

    Counts LINES, never occurrences of the word "serialize": pytest's own tmp
    directory names are derived from the test name, so a test about serializing
    puts that word inside every captured path and doubles its own count.
    """
    if not path.exists():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def _quiet(path: Path, seconds=1.0) -> None:
    """Assert a detached child never appears. There is nothing to poll FOR, so
    this waits out the window a real spawn would have needed."""
    time.sleep(seconds)
    assert not path.exists(), f"expected no spawn, got: {path.read_text()}"


def _log(home: Path) -> str:
    path = home / ".daimon" / "logs" / "serialize.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".kimi-code").mkdir(parents=True)
    return h


# ---- resolving a transcript from a session id ----

def test_the_library_resolves_a_transcript_from_the_session_id(home):
    """The payload names the session and nothing else. Measured: the payload's
    `session_id` IS the session directory name, one level below a workspace
    directory whose name daimon cannot predict, hence the glob."""
    lib = _load_lib()
    wire = _wire(home)
    found = lib.kimi_transcript(SESSION, home=home, env={})
    assert found == wire


def test_an_unknown_session_resolves_to_nothing(home):
    lib = _load_lib()
    _wire(home)
    assert lib.kimi_transcript("session_does-not-exist", home=home, env={}) is None


def test_a_session_id_carrying_a_separator_is_refused(home):
    """The id is host-supplied and lands straight in a glob pattern. This
    plants a REAL file the pattern would otherwise reach: without the guard,
    `sessions/*/a/b/agents/main/wire.jsonl` matches it and an id that is not a
    single path segment silently selects a transcript one level deeper than
    the layout allows. Nothing in the measured payloads suggests a hostile id,
    which is exactly why the assumption is pinned rather than trusted."""
    lib = _load_lib()
    nested = (home / ".kimi-code" / "sessions" / "wd_proj_1" / "a" / "b"
              / "agents" / "main" / "wire.jsonl")
    nested.parent.mkdir(parents=True)
    nested.write_text("{}\n", encoding="utf-8")
    assert nested.exists()
    assert lib.kimi_transcript("a/b", home=home, env={}) is None
    assert lib.kimi_transcript("a\\b", home=home, env={}) is None
    assert lib.kimi_transcript("..", home=home, env={}) is None
    assert lib.kimi_transcript("", home=home, env={}) is None


def test_the_resolver_refuses_glob_metacharacters_in_the_session_id(home):
    """The id is interpolated into `root.glob(...)`, so to the resolver `*`
    is not a name, it is a wildcard: an id of `*` resolves whichever real
    session sorts first, and the hook would then serialize a stranger's
    transcript under the attacker-supplied id. Traversal was already refused;
    this is the other class the same interpolation admits, and the one the
    guard's own docstring claimed to close."""
    lib = _load_lib()
    real = _wire(home)
    assert lib.kimi_transcript(SESSION, home=home, env={}) == real
    for hostile in ("*", "?", "session_*", f"[{SESSION[0]}]{SESSION[1:]}"):
        assert lib.kimi_transcript(hostile, home=home, env={}) is None, hostile


def test_the_resolver_honours_kimi_code_home(home, tmp_path):
    lib = _load_lib()
    relocated = tmp_path / "relocated"
    path = (relocated / "sessions" / "wd_x_1" / SESSION / "agents" / "main"
            / "wire.jsonl")
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    assert lib.kimi_transcript(
        SESSION, home=home, env={"KIMI_CODE_HOME": str(relocated)}) == path


def _load_lib():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "kimi_hook_lib_under_test", HOOK_DIR / "_daimon_hook_lib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- SessionEnd ----

def test_session_end_spawns_serialize_for_the_resolved_transcript(home):
    wire = _wire(home)
    bin_dir, capture = _fake_cli(home)
    proc = _run(SESSION_END_HOOK,
                {"hook_event_name": "SessionEnd", "session_id": SESSION,
                 "cwd": "/private/tmp/proj", "client_type": "kimi_code_cli",
                 "reason": "exit", "session_title": "t"},
                home, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode == 0
    invoked = _wait_for(capture, "serialize")
    assert str(wire) in invoked
    assert f"--session {SESSION}" in invoked, (
        "without --session the CLI would name every Kimi checkpoint 'wire'")
    assert "kimi-session-end: spawned serialize" in _log(home)


def test_session_end_records_a_reason_when_no_transcript_resolves(home):
    """Kimi is the only host where this is routine rather than broken: the
    payload has no path, so a lookup that finds nothing is the normal shape of
    "storage moved" and has to be legible in the log, not silent."""
    bin_dir, _ = _fake_cli(home)
    proc = _run(SESSION_END_HOOK,
                {"hook_event_name": "SessionEnd", "session_id": SESSION,
                 "cwd": "/private/tmp/proj", "reason": "exit"},
                home, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode == 0
    log = _log(home)
    assert "kimi-session-end" in log
    assert "transcript not found" in log
    assert SESSION in log


def test_session_end_serializes_even_when_stop_just_captured(home):
    """The Stop throttle window is the interval between the LAST spawned Stop
    and `/exit`, so a SessionEnd that honoured Stop's marker would drop up to
    a whole window of the session tail: every turn after that Stop. Codex
    leaves SessionEnd unthrottled for the same reason. The repeat is cheap:
    the CLI compares the transcript's sha against the last checkpoint before
    any LLM work, so bytes already captured cost one file read, not a second
    model call."""
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    payload = {"hook_event_name": "Stop", "session_id": SESSION,
               "cwd": "/private/tmp/proj", "stop_hook_active": False}
    _run(STOP_HOOK, payload, home, env)
    _wait_for(capture, "serialize")
    assert _spawns(capture) == 1
    _run(SESSION_END_HOOK, {**payload, "hook_event_name": "SessionEnd",
                            "reason": "exit"}, home, env)
    deadline = time.monotonic() + 10.0
    while _spawns(capture) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _spawns(capture) == 2, _log(home)
    assert "already captured" not in _log(home)
    assert "kimi-session-end: spawned serialize" in _log(home)


def test_session_end_can_be_disabled(home):
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    _run(SESSION_END_HOOK,
         {"hook_event_name": "SessionEnd", "session_id": SESSION,
          "cwd": "/p", "reason": "exit"}, home,
         {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
          "DAIMON_KIMI_SERIALIZE_ON_SESSION_END": "0"})
    _quiet(capture)


# ---- Stop ----

def test_stop_captures_a_print_mode_session(home):
    """The reason this hook is not optional on Kimi. `kimi -p` never fires
    SessionEnd, so without Stop a print session is never captured at all."""
    wire = _wire(home)
    bin_dir, capture = _fake_cli(home)
    proc = _run(STOP_HOOK,
                {"hook_event_name": "Stop", "session_id": SESSION,
                 "cwd": "/private/tmp/proj", "stop_hook_active": False},
                home, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode == 0
    invoked = _wait_for(capture, "serialize")
    assert str(wire) in invoked
    assert f"--session {SESSION}" in invoked
    assert "kimi-stop: spawned serialize" in _log(home)


def test_stop_is_throttled_within_the_interval(home):
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    payload = {"hook_event_name": "Stop", "session_id": SESSION, "cwd": "/p"}
    _run(STOP_HOOK, payload, home, env)
    _wait_for(capture, "serialize")
    _run(STOP_HOOK, payload, home, env)
    time.sleep(1.0)
    assert _spawns(capture) == 1
    assert "throttled" in _log(home)


def test_a_zero_interval_serializes_on_every_stop(home):
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "DAIMON_KIMI_MIN_SERIALIZE_INTERVAL": "0"}
    payload = {"hook_event_name": "Stop", "session_id": SESSION, "cwd": "/p"}
    _run(STOP_HOOK, payload, home, env)
    _wait_for(capture, "serialize")
    _run(STOP_HOOK, payload, home, env)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _spawns(capture) < 2:
        time.sleep(0.05)
    assert _spawns(capture) == 2


def test_stop_can_be_disabled(home):
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    _run(STOP_HOOK, {"hook_event_name": "Stop", "session_id": SESSION,
                     "cwd": "/p"}, home,
         {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
          "DAIMON_KIMI_SERIALIZE_ON_STOP": "0"})
    _quiet(capture)


# ---- UserPromptSubmit: the only channel into the model ----

PROJECT = "/private/tmp/kimi-proj"


def _checkpoint(sample_checkpoint):
    """Give this project a checkpoint for `daimon brief` to render."""
    from daimon_briefing import store

    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-kimi"
    store.write_checkpoint("S-kimi", mine, project_dir=PROJECT)


def _prompt(home, text="where were we", session=SESSION):
    """Run the prompt hook with the REAL CLI on PATH, which is what renders
    the briefing."""
    return _run(PROMPT_HOOK,
                {"hook_event_name": "UserPromptSubmit", "session_id": session,
                 "cwd": PROJECT, "client_type": "kimi_code_cli",
                 "prompt": [{"type": "text", "text": text}],
                 "is_steer": False}, home)


def test_the_briefing_is_emitted_on_the_first_prompt_of_a_session(
        home, tmp_checkpoint_dir, sample_checkpoint):
    """Kimi has no usable SessionStart, so the briefing rides the first prompt.
    Plain stdout, no JSON envelope: the host appends what the hook prints,
    wrapped in a `hook_result` tag of its own."""
    _checkpoint(sample_checkpoint)
    proc = _prompt(home)
    assert proc.returncode == 0
    assert "DAIMON BRIEFING" in proc.stdout
    assert "checkpoint: S-kimi" in proc.stdout


def test_the_briefing_is_emitted_only_once_per_session(
        home, tmp_checkpoint_dir, sample_checkpoint):
    """The second prompt of a session must not repeat it. The host gives no
    session-start event to hang "first" on, so the hook keeps its own marker."""
    _checkpoint(sample_checkpoint)
    first = _prompt(home)
    second = _prompt(home)
    assert "DAIMON BRIEFING" in first.stdout
    assert "DAIMON BRIEFING" not in second.stdout


def test_a_different_session_gets_its_own_briefing(
        home, tmp_checkpoint_dir, sample_checkpoint):
    _checkpoint(sample_checkpoint)
    _prompt(home)
    other = _prompt(home, session="session_other")
    assert "DAIMON BRIEFING" in other.stdout


def test_no_checkpoint_means_no_output_at_all(home, tmp_checkpoint_dir):
    """This hook fires on EVERY prompt. A diagnostic line per prompt would be
    spam, so silence is the contract when there is nothing to say."""
    proc = _prompt(home)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_the_prompt_part_list_is_flattened_for_recall(home):
    """Kimi's `prompt` is a LIST of parts, where every other host sends a
    string. Handed to `recall-inject` unflattened it would arrive as a Python
    repr, and every match would be against punctuation."""
    lib = _load_lib()
    assert lib.kimi_prompt_text(
        [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]
    ) == "first\nsecond"
    # A host that later sends a bare string must keep working.
    assert lib.kimi_prompt_text("plain") == "plain"
    assert lib.kimi_prompt_text(None) == ""
    assert lib.kimi_prompt_text([{"type": "image", "url": "x"}]) == ""


def test_recall_runs_on_a_later_prompt_and_not_on_a_slash_command(home):
    """Same noise gate as the Claude Code prompt hook: a slash command is a
    host directive, not somebody asking about prior work."""
    bin_dir, capture = _fake_cli(home)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    base = {"hook_event_name": "UserPromptSubmit", "session_id": SESSION,
            "cwd": PROJECT}
    _run(PROMPT_HOOK, {**base, "prompt": [{"type": "text", "text": "hi"}]},
         home, env)  # first prompt of the session
    _run(PROMPT_HOOK,
         {**base, "prompt": [{"type": "text", "text": "what about the parser"}]},
         home, env)
    text = capture.read_text(encoding="utf-8")
    assert "recall-inject" in text
    before = text.count("recall-inject")
    _run(PROMPT_HOOK, {**base, "prompt": [{"type": "text", "text": "/help"}]},
         home, env)
    assert capture.read_text(encoding="utf-8").count("recall-inject") == before


# ---- every Kimi transcript is named wire.jsonl ----
#
# `_run_serialize` derives the session id as `path.stem`, which is true of
# every host adapted so far because each names the transcript for its session.
# Kimi does not: the session id is a DIRECTORY, and the file inside it is
# always `wire.jsonl`. Left alone, every Kimi session on the machine would
# serialize under the id "wire", overwrite the previous one's per-session
# checkpoint, and make the in-flight guard treat two unrelated sessions as the
# same work. So `serialize` gained `--session`, following the same rule #983
# set for `write-checkpoint`: when the host can supply a real session id, that
# id wins over anything inferred.

def _serialize_session_id(tmp_path, monkeypatch, session=None):
    """The session id `_run_serialize` actually adopts for a transcript.

    Captured at the first place the id is USED (the identical-bytes guard)
    rather than asserted from a checkpoint on disk: without an LLM backend the
    run fails long before it writes one, so an on-disk assertion would pass
    whether the flag worked or not."""
    from daimon_briefing import cli as cli_mod

    seen = []
    monkeypatch.setattr(cli_mod.store, "transcript_unchanged",
                        lambda sid, sha: seen.append(sid) or True)
    wire = tmp_path / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text(
        json.dumps({"type": "metadata", "protocol_version": "1.5"}) + "\n",
        encoding="utf-8")
    cli_mod._run_serialize(wire, PROJECT, session=session)
    assert seen, "the session id was never used"
    return seen[0]


def test_serialize_takes_the_session_id_from_the_flag_not_the_filename(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    assert _serialize_session_id(tmp_path, monkeypatch, session=SESSION) == SESSION


def test_without_the_flag_the_filename_still_wins_as_it_always_has(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    """The other half: every host adapted before Kimi passes no `--session`,
    and their behavior has to be unchanged. On Kimi the stem is the constant
    "wire", which is the whole reason the flag exists."""
    assert _serialize_session_id(tmp_path, monkeypatch) == "wire"


def test_the_serialize_flag_is_reachable_from_the_command_line(tmp_path):
    """The parser half. A flag `_run_serialize` honours and the argument
    parser never defines is a flag no hook can actually pass."""
    proc = subprocess.run(
        [sys.executable, "-m", "daimon_briefing.cli", "serialize", "--help"],
        capture_output=True, text=True, timeout=60, env=os.environ)
    # Word-bounded: a plain substring check also matches `--sessionX`, so a
    # renamed flag would pass a test written to catch a MISSING one.
    assert re.search(r"--session\b", proc.stdout), proc.stdout


def test_two_kimi_sessions_do_not_collapse_into_one_checkpoint(home):
    """The failure this flag exists to prevent, stated as behavior rather than
    as an argument about filenames."""
    lib = _load_lib()
    calls = []

    class _Fake:
        def __init__(self, cmd, **kw):
            calls.append(cmd)

    import subprocess as sp
    real = sp.Popen
    sp.Popen = _Fake
    try:
        lib.spawn_serialize("daimon", "/s/a/agents/main/wire.jsonl", {},
                            session_id="session_a")
        lib.spawn_serialize("daimon", "/s/b/agents/main/wire.jsonl", {},
                            session_id="session_b")
    finally:
        sp.Popen = real
    ids = [cmd[cmd.index("--session") + 1] for cmd in calls]
    assert ids == ["session_a", "session_b"]


def test_the_in_flight_guard_keys_on_the_supplied_session_id(home, monkeypatch):
    """Without the override the guard keys on the stem, so a live serialize of
    ANY Kimi session would report every other Kimi session as in flight and
    silently drop its capture."""
    lib = _load_lib()
    monkeypatch.setattr(lib, "_in_flight_stems", lambda: {"session_a"})
    assert lib._serialize_in_flight("/s/a/agents/main/wire.jsonl",
                                    session_id="session_a") is True
    assert lib._serialize_in_flight("/s/b/agents/main/wire.jsonl",
                                    session_id="session_b") is False


# ---- fail-open, on every script ----

@pytest.mark.parametrize("script", [PROMPT_HOOK, SESSION_END_HOOK, STOP_HOOK])
@pytest.mark.parametrize("payload", ["", "not json", "[]", "{}"])
def test_no_script_crashes_the_host_on_a_bad_payload(script, payload, home):
    """Kimi is fail-open by design, but a non-zero exit from a BLOCKABLE event
    is how a hook denies. `UserPromptSubmit` is blockable, so a crashing hook
    here would not merely be noisy, it would refuse the person's prompt."""
    proc = _run(script, payload, home)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("script", [PROMPT_HOOK, SESSION_END_HOOK, STOP_HOOK])
def test_a_partial_install_missing_the_library_still_exits_clean(script, home,
                                                                 tmp_path):
    stray = tmp_path / "stray"
    stray.mkdir()
    copy = stray / script.name
    copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    proc = _run(copy, {"session_id": SESSION, "cwd": "/p",
                       "prompt": [{"type": "text", "text": "hi"}]}, home)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("script", [PROMPT_HOOK, SESSION_END_HOOK, STOP_HOOK])
def test_the_global_disable_silences_every_script(script, home):
    _wire(home)
    bin_dir, capture = _fake_cli(home)
    proc = _run(script, {"session_id": SESSION, "cwd": "/p",
                         "prompt": [{"type": "text", "text": "hi"}]}, home,
                {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                 "DAIMON_DISABLE": "1"})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    _quiet(capture)
