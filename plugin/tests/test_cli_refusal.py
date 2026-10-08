"""A person at the CLI is told why a write was refused (#1132 PR 10b, D10.3).

`cli.main` runs every verb with refusals surfaced: a module appender raises
`jsonl.Refused` instead of answering False, and the one central handler
prints `error: <body>` and exits 2. A library caller keeps the contract every
module appender always had: not written is False.
"""

import json

import pytest

from daimon_briefing import (amendments, cli, jsonl, relations, requests,
                             store, trust)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/cli-refusal"
GARBAGE = b"<<<<<<< conflict\n"


def _break(name, project=PROJECT):
    path = store._events_path(project).parent / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)
    return path


def test_a_handoff_on_a_garbage_events_ledger_exits_2_and_says_why(
        tmp_checkpoint_dir, capsys):
    _break("events.jsonl")
    rc = cli.main(["handoff", "Ship it.", "--project", PROJECT])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.strip() == ("error: events.jsonl is unreadable (garbage); "
                           "run: daimon ledger repair events")
    assert store._events_path(PROJECT).read_bytes() == GARBAGE


def test_log_exits_2_through_the_same_handler(tmp_checkpoint_dir, capsys):
    _break("events.jsonl")
    rc = cli.main(["log", "--text", "something happened", "--kind", "note",
                   "--project", PROJECT])
    assert rc == 2
    assert capsys.readouterr().err.startswith("error: events.jsonl is ")


def test_a_trust_propose_on_a_garbage_trust_ledger_names_the_trust_repair(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _break("trust.jsonl")
    rc = cli.main([
        "trust", "propose", "--text", "the runbook step was fabricated",
        "--kind", "decision", "--reason", "no PR", "--evidence", "issue:1",
        "--project", PROJECT])
    assert rc == 2
    assert capsys.readouterr().err.strip() == (
        "error: trust.jsonl is unreadable (garbage); "
        "run: daimon trust repair")


def test_a_refused_trust_confirm_is_not_reported_as_an_unknown_id(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # The ledger cannot be read, so "no such quarantine" would be a lie.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _break("trust.jsonl")
    rc = cli.main(["trust", "confirm", "q-aaaaaaaaaaaa",
                   "--project", PROJECT])
    assert rc == 2
    assert "trust.jsonl is unreadable" in capsys.readouterr().err


def test_a_request_open_on_a_garbage_requests_ledger_exits_2(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _break("requests.jsonl")
    rc = cli.main(["request", "open", "--to", "other", "--ask", "do a thing",
                   "--why", "because", "--anyway", "--project", PROJECT])
    assert rc == 2
    assert "requests.jsonl is unreadable" in capsys.readouterr().err


def test_a_healthy_ledger_is_untouched_by_the_handler(tmp_checkpoint_dir):
    assert cli.main(["handoff", "Ship it.", "--project", PROJECT]) == 0


# ---- library callers keep "not written" as False ----------------------------

@pytest.mark.parametrize("call", [
    pytest.param(lambda: store.append_event(
        "o-1", "resolved", project_dir=PROJECT, writer=Writer.HUMAN),
        id="events"),
    pytest.param(lambda: trust.append(
        {"event": "proposed"}, project_dir=PROJECT, writer=Writer.HUMAN),
        id="trust"),
    pytest.param(lambda: requests.append(
        {"event": "opened"}, project_dir=PROJECT, writer=Writer.HUMAN),
        id="requests"),
    pytest.param(lambda: amendments.append(
        {"event": "proposed"}, project_dir=PROJECT, writer=Writer.HUMAN),
        id="amendments"),
    pytest.param(lambda: relations._append(
        {"event": "proposed"}, project_dir=PROJECT, writer=Writer.HUMAN),
        id="relations"),
])
def test_a_library_caller_gets_false_not_an_exception(tmp_checkpoint_dir, call):
    for name in ("events.jsonl", "trust.jsonl", "requests.jsonl",
                 "amendments.jsonl", "relations.jsonl"):
        _break(name)
    assert call() is False


def test_the_cli_run_alone_surfaces_the_refusal(tmp_checkpoint_dir):
    _break("events.jsonl")
    with jsonl.surface_refusals():
        with pytest.raises(jsonl.Refused):
            store.append_event("o-1", "resolved", project_dir=PROJECT,
                               writer=Writer.HUMAN)
    assert store.append_event("o-1", "resolved", project_dir=PROJECT,
                              writer=Writer.HUMAN) is False


def test_an_emitter_is_skipped_never_refused(tmp_checkpoint_dir):
    path = _break("events.jsonl")
    with jsonl.surface_refusals():
        assert store.append_event(
            "o-1", "supersede-candidate:o-2", project_dir=PROJECT,
            source="serializer", writer=Writer.EMITTER) is False
    assert path.read_bytes() == GARBAGE


def test_the_body_is_one_line_of_json_free_text(tmp_checkpoint_dir, capsys):
    _break("events.jsonl")
    cli.main(["handoff", "x", "--project", PROJECT])
    line = capsys.readouterr().err.strip()
    assert "\n" not in line
    with pytest.raises(ValueError):
        json.loads(line)
