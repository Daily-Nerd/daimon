"""The next briefing of a project names the sessions it could not serialize
(#1132 PR 10b, D10.4): CLI `brief` and `loops`, the MCP tool and the Hermes
injection all carry the `admission-refused` note from `briefing.prepare`.
"""

import time

import pytest

from daimon_briefing import briefing, cli, config, hooks, mcp_tools, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/admission-brief"
OTHER = "/p/admission-other"
HINT = "run: daimon ledger repair events"
NOTE = ("⚠ 2 session(s) of this project not serialized: events.jsonl is "
        f"unreadable; {HINT}, then daimon heal")


def _seed(project):
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": "keep going", "trust": "inferred"}],
            "open_questions": [{"text": "who owns it", "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=project, writer=Writer.HUMAN)


def _refuse(project, n=2):
    path = store._events_path(project)
    path.write_bytes(b"<<<<<<< conflict\n")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    lines = []
    for i in range(n):
        lines.append(f"{stamp} session-end: spawned serialize for held-{i} "
                     f"(reason: x, project: {project}) "
                     f"(transcript: /t/held-{i}.jsonl)")
        lines.append(f"error: admission refused: events.jsonl is unreadable; "
                     f"{HINT} (transcript: /t/held-{i}.jsonl) after 0s")
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text("\n".join(lines) + "\n")


@pytest.fixture
def refused(tmp_checkpoint_dir, monkeypatch):
    _seed(PROJECT)
    _seed(OTHER)
    _refuse(PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


def test_prepare_carries_the_note(refused):
    annotated = briefing.prepare(PROJECT, time.time())
    assert NOTE in annotated.notes


def test_cli_brief_shows_it(refused, capsys):
    assert cli.main(["brief"]) == 0
    assert NOTE in capsys.readouterr().out


def test_cli_loops_shows_it(refused, capsys):
    assert cli.main(["loops"]) == 0
    assert NOTE in capsys.readouterr().out


def test_the_mcp_brief_shows_it(refused):
    result = mcp_tools.HANDLERS["daimon_brief"]({"project": PROJECT})
    assert NOTE in result.text


def test_the_hermes_injection_shows_it(refused):
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert NOTE in out["context"]


def test_another_project_does_not_see_it(refused, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", OTHER)
    assert cli.main(["brief"]) == 0
    assert "not serialized" not in capsys.readouterr().out


def test_a_proven_ledger_says_nothing(tmp_checkpoint_dir, monkeypatch, capsys):
    _seed(PROJECT)
    _refuse(PROJECT)
    store._events_path(PROJECT).write_bytes(b"")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief"]) == 0
    assert "not serialized" not in capsys.readouterr().out


def test_a_refusal_beyond_the_200_line_tail_is_still_briefed(refused, capsys):
    log = config.log_dir() / "serialize.log"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    noise = "".join(
        f"{stamp} session-end: spawned serialize for n-{i} "
        f"(reason: x, project: /elsewhere) (transcript: /nope/n-{i}.jsonl)\n"
        for i in range(300))
    log.write_text(log.read_text() + noise)
    assert cli.main(["brief"]) == 0
    assert NOTE in capsys.readouterr().out
