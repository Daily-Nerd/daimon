"""`daimon serialize --host <name>`: a host-run serialize announces itself (#1314).

A host that runs the agent runtime in its own sandbox cannot let the
SessionEnd hook spawn the serialize, so it runs the CLI itself. The hook's
spawn line is the ledger's only carrier of `project:`, its in-flight guard is
the only duplicate refusal, and its stderr redirect is the only crash record.
With `--host` the CLI owns all three; without it nothing changes.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from daimon_briefing import cli, heal, ledger, llm, store
from tests.conftest import FIXTURES  # noqa: F401  (conftest import keeps isolation)

HOST = "my-runner"
SID = "S-host-run-1"
DOCS_PAGE = Path(__file__).resolve().parents[2] / "website" / "docs" / "hosts" / "any-runtime.md"


def _valid_json(session_id):
    return json.dumps({
        "session_id": session_id,
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [{"text": "q", "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    })


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return cli._resolve_project(str(p))


@pytest.fixture
def transcript(tmp_path):
    t = tmp_path / f"{SID}.jsonl"
    rows = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"line {i} from host"} for i in range(8)]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return t


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    # setenv-then-delenv records the original (absent) state, so a value the
    # command puts into os.environ is undone at teardown.
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "x")
    monkeypatch.delenv("DAIMON_CAPTURE_HOST")


def _use_chat(monkeypatch, fake_chat_factory, response=None):
    chat = fake_chat_factory(response if response is not None else _valid_json(SID))
    monkeypatch.setattr(cli, "_chat", chat)
    return chat


def _log(log_dir):
    p = log_dir / "serialize.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _beat(log_dir, name, age):
    d = log_dir / "heartbeats"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("", encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def _argv(transcript, project, *extra):
    return ["serialize", str(transcript), "--project", project, *extra]


# ---- the spawn line ---------------------------------------------------------


def test_host_run_writes_the_spawn_line_before_the_result_line(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    assert cli.main(_argv(transcript, project, "--host", HOST)) == 0
    lines = _log(log_dir).splitlines()
    spawn_at = next(i for i, ln in enumerate(lines) if ledger._SPAWN_RE.match(ln))
    result_at = next(i for i, ln in enumerate(lines) if ln.startswith("wrote checkpoint: "))
    assert spawn_at < result_at
    spawn = lines[spawn_at]
    assert spawn.split(" ", 1)[1] == (
        f"{HOST}: spawned serialize for {SID} "
        f"(reason: host-run, project: {project}) (transcript: {transcript})"
    )
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", spawn)
    assert ledger._SPAWN_RE.match(spawn).group(2) == SID


def test_the_fold_attributes_a_host_run_to_its_project(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    cli.main(_argv(transcript, project, "--host", HOST))
    e = ledger._session_ledger(_log(log_dir), now=time.time())[SID]
    assert e["spawned"] is True
    assert e["project"] == project
    assert e["reason"] == "host-run"
    assert e["result_kind"] == "success"


def test_a_failed_host_run_heals_with_the_project_named(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory, llm.ChatError("boom"))
    assert cli.main(_argv(transcript, project, "--host", HOST)) == 1
    plan = heal.plan_all(_log(log_dir), time.time())
    assert [t.session_id for t in plan.targets] == [SID]
    assert plan.targets[0].project == project
    assert all(s.reason != "project-unknown" for s in plan.skipped)


def test_host_run_is_not_an_import_so_heal_may_retry_it(transcript):
    line = (f"2026-07-03T23:00:00Z {HOST}: spawned serialize for {SID} "
            f"(reason: host-run, project: /p/A) (transcript: {transcript})")
    text = line + f"\nerror: boom (transcript: {transcript}) after 9s"
    out = ledger._compute_outstanding(text, now=1_800_000_000.0)
    assert [f["class"] for f in out] == ["healable"]


def test_stats_counts_a_host_run_under_its_name():
    line = (f"2026-07-03T23:00:00Z {HOST}: spawned serialize for {SID} "
            f"(reason: host-run, project: /p/A) (transcript: /t/{SID}.jsonl)")
    assert ledger._STATS_HOST_RE.match(line)
    other = (f"2026-07-03T23:00:00Z {HOST}: spawned serialize for {SID} "
             f"(reason: exit, project: /p/A)")
    assert not ledger._SPAWN_RE.match(other), (
        "an unknown name is a host only when the line says host-run")


# ---- without --host nothing changes -----------------------------------------


def test_without_host_there_is_no_spawn_line_and_the_result_is_raw(
    transcript, project, log_dir, monkeypatch, fake_chat_factory, capsys
):
    _use_chat(monkeypatch, fake_chat_factory)
    assert cli.main(_argv(transcript, project)) == 0
    lines = [ln for ln in _log(log_dir).splitlines()
             if not re.match(r"^\S+ (INFO|WARNING|ERROR) ", ln)]
    assert not any(ledger._SPAWN_RE.match(ln) for ln in lines)
    assert lines[-1].startswith("wrote checkpoint: ")
    assert lines[-1] == capsys.readouterr().out.strip().splitlines()[-1]
    assert "DAIMON_CAPTURE_HOST" not in os.environ


# ---- the in-flight guard ----------------------------------------------------


def test_a_live_heartbeat_refuses_the_run_with_75(
    transcript, project, log_dir, tmp_checkpoint_dir, monkeypatch,
    fake_chat_factory, capsys
):
    chat = _use_chat(monkeypatch, fake_chat_factory)
    beat = _beat(log_dir, SID, age=5)
    rc = cli.main(_argv(transcript, project, "--host", HOST))
    assert rc == 75
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "already in flight" in err[0] and SID in err[0]
    assert chat.calls == []
    assert store.read_checkpoint(SID) is None
    assert beat.exists(), "the guard must not clear the live run's stamp"
    log = _log(log_dir)
    assert re.search(
        rf"^\S+ {HOST}: skipped serialize for {SID} \(already in flight\)$",
        log, re.M)
    assert not any(ledger._SPAWN_RE.match(ln) for ln in log.splitlines()), (
        "a refused run must not announce a spawn it never made")
    e = ledger._session_ledger(log, now=time.time()).get(SID)
    assert e is None or e["result_kind"] is None, (
        "the skip line must not close the live run's classification")


def test_the_guard_keys_on_the_named_session_not_the_file_stem(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    _beat(log_dir, "S-named", age=5)
    rc = cli.main(_argv(transcript, project, "--host", HOST, "--session", "S-named"))
    assert rc == 75
    _beat(log_dir, "S-other", age=5)  # a stamp for the stem is not this run's
    (log_dir / "heartbeats" / "S-named").unlink()
    (log_dir / "heartbeats" / "S-other").rename(log_dir / "heartbeats" / SID)
    rc = cli.main(_argv(transcript, project, "--host", HOST, "--session", "S-named"))
    assert rc == 0


def test_a_stale_heartbeat_does_not_block_the_run(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    _beat(log_dir, SID, age=3600)
    monkeypatch.setenv("DAIMON_HUNG_AFTER", "1800")
    assert cli.main(_argv(transcript, project, "--host", HOST)) == 0
    assert store.read_checkpoint(SID) is not None


def test_without_host_a_live_heartbeat_is_not_refused(
    transcript, project, log_dir, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    _beat(log_dir, SID, age=5)
    assert cli.main(_argv(transcript, project)) == 0


# ---- provenance -------------------------------------------------------------


def _record_host(monkeypatch):
    seen = {}

    def fake(path, project, session=None, **kw):
        seen["host"] = os.environ.get("DAIMON_CAPTURE_HOST")
        return 0

    monkeypatch.setattr(cli, "_run_serialize", fake)
    return seen


def test_capture_host_is_set_from_host_when_absent(
    transcript, project, monkeypatch
):
    seen = _record_host(monkeypatch)
    cli.main(_argv(transcript, project, "--host", HOST))
    assert seen["host"] == HOST


def test_capture_host_already_in_the_environment_wins(
    transcript, project, monkeypatch
):
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    seen = _record_host(monkeypatch)
    cli.main(_argv(transcript, project, "--host", HOST))
    assert seen["host"] == "claude-code"


# ---- the crash record -------------------------------------------------------


def _explode(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom in the pipeline")

    monkeypatch.setattr(cli, "_run_serialize", boom)


def test_an_escaping_exception_is_recorded_then_reraised(
    transcript, project, log_dir, monkeypatch
):
    _explode(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["daimon", *_argv(transcript, project, "--host", HOST)])
    with pytest.raises(RuntimeError, match="boom in the pipeline"):
        cli.main(_argv(transcript, project, "--host", HOST))
    text = (log_dir / "serialize-crash.log").read_text(encoding="utf-8")
    assert re.search(r"^--- crash \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ pid=\d+ cmd=serialize ---$",
                     text, re.M)
    assert "RuntimeError: boom in the pipeline" in text


def test_the_crash_record_is_redacted(transcript, project, log_dir, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("leak sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")

    monkeypatch.setattr(cli, "_run_serialize", boom)
    with pytest.raises(RuntimeError):
        cli.main(_argv(transcript, project, "--host", HOST))
    text = (log_dir / "serialize-crash.log").read_text(encoding="utf-8")
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in text


def test_the_crash_log_is_trimmed_at_the_cap(
    transcript, project, log_dir, monkeypatch
):
    _explode(monkeypatch)
    crash = log_dir / "serialize-crash.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    crash.write_bytes(b"o" * (ledger.CRASH_LOG_MAX_BYTES + 4096))
    with pytest.raises(RuntimeError):
        cli.main(_argv(transcript, project, "--host", HOST))
    data = crash.read_bytes()
    assert len(data) < ledger.CRASH_LOG_MAX_BYTES
    assert data.startswith(b"o" * 1000)
    assert b"RuntimeError: boom in the pipeline" in data


def test_a_crash_under_the_cap_keeps_the_earlier_entries(
    transcript, project, log_dir, monkeypatch
):
    _explode(monkeypatch)
    crash = log_dir / "serialize-crash.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    crash.write_text("--- crash earlier ---\nold\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        cli.main(_argv(transcript, project, "--host", HOST))
    text = crash.read_text(encoding="utf-8")
    assert text.startswith("--- crash earlier ---\nold\n")
    assert "boom in the pipeline" in text


def test_a_crash_without_host_writes_no_record(
    transcript, project, log_dir, monkeypatch
):
    _explode(monkeypatch)
    with pytest.raises(RuntimeError):
        cli.main(_argv(transcript, project))
    assert not (log_dir / "serialize-crash.log").exists()


def test_an_unwritable_log_dir_does_not_mask_the_original_exception(
    transcript, project, log_dir, monkeypatch
):
    _explode(monkeypatch)
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("a file where the directory should be", encoding="utf-8")
    with pytest.raises(RuntimeError, match="boom in the pipeline"):
        cli.main(_argv(transcript, project, "--host", HOST))


def test_a_failing_redaction_leaves_the_header_and_never_the_raw_traceback(
    transcript, project, log_dir, monkeypatch
):
    from daimon_briefing import redact

    def broken(text):
        raise ValueError("redactor down")

    monkeypatch.setattr(redact, "redact_text", broken)

    def boom(*a, **k):
        raise RuntimeError("leak-me-not")

    monkeypatch.setattr(cli, "_run_serialize", boom)
    with pytest.raises(RuntimeError):
        cli.main(_argv(transcript, project, "--host", HOST))
    text = (log_dir / "serialize-crash.log").read_text(encoding="utf-8")
    assert text.startswith("--- crash ")
    assert "leak-me-not" not in text


def test_an_unwritable_log_dir_loses_the_skip_line_without_raising(log_dir):
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("a file", encoding="utf-8")
    ledger.write_host_skip_line(HOST, SID)


def test_crash_caps_mirror_the_hook_library():
    import importlib.util

    src = (Path(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "_daimon_hook_lib.py")
    spec = importlib.util.spec_from_file_location("_hook_lib_caps", src)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    assert ledger.CRASH_LOG_MAX_BYTES == lib.CRASH_LOG_MAX_BYTES
    assert ledger.CRASH_LOG_KEEP_BYTES == lib.CRASH_LOG_KEEP_BYTES


# ---- the host name ----------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "", "A", "My-Runner", "-x", "a b", "a/b", "a_b", "x" * 41, "é", "a.b",
])
def test_a_bad_host_name_is_refused_with_exit_2(
    bad, transcript, project, log_dir, monkeypatch, fake_chat_factory, capsys
):
    chat = _use_chat(monkeypatch, fake_chat_factory)
    with pytest.raises(SystemExit) as exc:
        cli.main(_argv(transcript, project, f"--host={bad}"))
    assert exc.value.code == 2
    assert "invalid host name" in capsys.readouterr().err
    assert chat.calls == []
    assert _log(log_dir) == ""


@pytest.mark.parametrize("good", ["x", "claude-code", "codex", "my-runner", "a" * 40, "0a-1"])
def test_a_good_host_name_is_accepted(
    good, transcript, project, monkeypatch, fake_chat_factory
):
    _use_chat(monkeypatch, fake_chat_factory)
    assert cli.main(_argv(transcript, project, "--host", good)) == 0


# ---- the docs page is the parser's fixture ----------------------------------


def _docs_jsonl():
    text = DOCS_PAGE.read_text(encoding="utf-8")
    m = re.search(r"```jsonl\n(.*?)```", text, re.S)
    assert m, "any-runtime.md must carry one ```jsonl example"
    return m.group(1)


def test_the_docs_example_serializes_to_its_own_turn_count(
    tmp_path, project, monkeypatch, fake_chat_factory
):
    body = _docs_jsonl()
    rows = [json.loads(ln) for ln in body.splitlines() if ln.strip()]
    t = tmp_path / f"{SID}.jsonl"
    t.write_text(body, encoding="utf-8")
    from daimon_briefing import transcript as tr

    msgs = tr.from_file_detailed(t)["messages"]
    assert [m["role"] for m in msgs] == [r["role"] for r in rows]
    assert [m["content"] for m in msgs] == [r["content"] for r in rows]
    chat = _use_chat(monkeypatch, fake_chat_factory)
    assert cli.main(_argv(t, project, "--host", HOST)) == 0
    assert store.read_checkpoint(SID) is not None
    assert chat.calls
