"""A result line written by a serialize that knows its session names it (#1317).

Kimi Code names every transcript `wire.jsonl` and always passes `--session`; a
host-run (`--host`) may do the same. The ledger keys an error line by its
`(session: <id>)` group when present and by the transcript stem otherwise, so
every failure kind has to carry the group or the failure lands on a session
called `wire` with no spawn line and no project.
"""

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest

from daimon_briefing import cli, heal, ledger, llm

SID = "s1"
PROJECT = "/work/kimi-proj"
HOST = "my-runner"
NOW = time.time()
STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 120))

HOOK_LIBS = (Path(__file__).resolve().parents[2] / "hook" / "_daimon_hook_lib.py",
             Path(ledger.__file__).parent / "_hooks" / "_daimon_hook_lib.py")


def _hook_lib(path):
    spec = importlib.util.spec_from_file_location("_daimon_hook_lib_k", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "x")
    monkeypatch.delenv("DAIMON_CAPTURE_HOST")


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"


@pytest.fixture
def wire(tmp_path):
    """A Kimi-shaped transcript: the session id is the directory."""
    d = tmp_path / "kimi" / SID
    d.mkdir(parents=True)
    t = d / "wire.jsonl"
    rows = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"line {i} from kimi"} for i in range(8)]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return t


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return cli._resolve_project(str(p))


def _failing_chat(monkeypatch):
    def chat(messages, **kw):
        raise llm.ChatError("boom")
    monkeypatch.setattr(cli, "_chat", chat)


def _no_key(monkeypatch):
    """Pre-flight fires only when the real llm.chat is wired and no backend
    resolves (no rescue command either, #383)."""
    monkeypatch.setattr(cli, "_chat", llm.chat)
    for var in ("DAIMON_LLM_API_KEY", "LITELLM_API_KEY", "DAIMON_LLM_MODEL",
                "LITELLM_MODEL", "DAIMON_LLM_COMMAND"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(cli.config, "llm_api_key", lambda: None)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)


def _seed_spawn(log_dir, wire):
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "serialize.log", "a", encoding="utf-8") as f:
        f.write(f"{STAMP} kimi-stop: spawned serialize for {SID} "
                f"(reason: x, project: {PROJECT}) (transcript: {wire})\n")


def _text(log_dir):
    return (log_dir / "serialize.log").read_text(encoding="utf-8")


def _last(log_dir):
    return _text(log_dir).strip().splitlines()[-1]


def _assert_one_session(log_dir, project=PROJECT):
    entries = ledger._session_ledger(_text(log_dir), now=time.time())
    assert set(entries) == {SID}, "the failure must not open a `wire` entry"
    e = entries[SID]
    assert e["spawned"] is True
    assert e["project"] == project
    assert e["result_kind"] == "error"


# ---- 1: SerializeError ------------------------------------------------------

def test_a_serialize_error_names_the_session_it_was_told(
        wire, project, log_dir, monkeypatch):
    _seed_spawn(log_dir, wire)
    _failing_chat(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--session", SID]) == 1
    assert re.match(r"^error: .* \(session: s1\) "
                    rf"\(transcript: {re.escape(str(wire))}\) after \d+s$",
                    _last(log_dir))
    _assert_one_session(log_dir)


# ---- 2: pre-flight ----------------------------------------------------------

def test_a_preflight_error_names_the_session_it_was_told(
        wire, project, log_dir, monkeypatch):
    _seed_spawn(log_dir, wire)
    _no_key(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--session", SID]) == 1
    assert _last(log_dir) == (
        "error: no LLM API key — set DAIMON_LLM_API_KEY "
        f"(env or ~/.daimon/env) (session: s1) (transcript: {wire})")
    _assert_one_session(log_dir)


def test_a_missing_model_names_the_session_it_was_told(
        wire, project, log_dir, monkeypatch):
    _no_key(monkeypatch)
    monkeypatch.setattr(cli.config, "llm_api_key", lambda: "k")
    monkeypatch.setattr(cli.config, "llm_model", lambda: None)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--session", SID]) == 1
    assert _last(log_dir) == (
        "error: no LLM model \u2014 set DAIMON_LLM_MODEL "
        f"(env or ~/.daimon/env) (session: s1) (transcript: {wire})")


# ---- 3: the host-run door ---------------------------------------------------

def test_a_host_run_error_names_its_session(wire, project, log_dir, monkeypatch):
    _failing_chat(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--host", HOST, "--session", SID]) == 1
    assert "(session: s1) (transcript: " in _last(log_dir)
    _assert_one_session(log_dir, project)


def test_a_host_run_preflight_error_names_its_session(
        wire, project, log_dir, monkeypatch):
    _no_key(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--host", HOST, "--session", SID]) == 1
    assert "(session: s1) (transcript: " in _last(log_dir)
    _assert_one_session(log_dir, project)


# ---- 4: without --session nothing moves -------------------------------------

def test_without_session_the_serialize_error_line_is_unchanged(
        wire, project, log_dir, monkeypatch):
    _failing_chat(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project]) == 1
    last = _last(log_dir)
    assert "(session:" not in last
    assert re.match(r"^error: .*? \(transcript: "
                    rf"{re.escape(str(wire))}\) after \d+s$", last)


def test_without_session_the_preflight_line_is_unchanged(
        wire, project, log_dir, monkeypatch):
    _no_key(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project]) == 1
    assert _last(log_dir) == (
        "error: no LLM API key — set DAIMON_LLM_API_KEY "
        f"(env or ~/.daimon/env) (transcript: {wire})")


def test_a_blank_session_is_not_a_named_session(
        wire, project, log_dir, monkeypatch):
    _no_key(monkeypatch)
    assert cli.main(["serialize", str(wire), "--project", project,
                     "--session", "  "]) == 1
    assert "(session:" not in _last(log_dir)


# ---- 5: the hook library's twin ---------------------------------------------

@pytest.mark.parametrize("lib_path", HOOK_LIBS, ids=["hook", "packaged"])
def test_the_hook_library_keys_the_named_error_to_the_session(
        lib_path, wire, project, log_dir, monkeypatch):
    _seed_spawn(log_dir, wire)
    _failing_chat(monkeypatch)
    cli.main(["serialize", str(wire), "--project", project, "--session", SID])
    monkeypatch.setenv("DAIMON_LOG_DIR", str(log_dir))
    assert SID in _hook_lib(lib_path)._failed_session_stems()


# ---- 6: heal ----------------------------------------------------------------

def test_heal_plans_the_real_session_with_its_project_on_the_first_pass(
        wire, project, log_dir, monkeypatch):
    _seed_spawn(log_dir, wire)
    _failing_chat(monkeypatch)
    cli.main(["serialize", str(wire), "--project", project, "--session", SID])
    plan = heal.plan_all(_text(log_dir), time.time())
    assert [t.session_id for t in plan.targets] == [SID]
    assert plan.targets[0].project == PROJECT
    assert all(s.reason != "project-unknown" for s in plan.skipped)
