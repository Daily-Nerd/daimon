"""#1292: a transcript with rows but no admitted conversation is a read failure.

Scar 0045 named the class: a loader that cannot read a host file returns [],
and the too-short gate then reports the loss as a policy skip (or the hook
returns without a line), so `status` never counts it and `heal` never retries
it. The invariant these tests pin:

  every non-empty line of a transcript lands in exactly one bucket (admitted,
  excluded with a code from EXCLUDED_ROWS, or dropped with a code from
  DROP_REASONS), and rows present with zero conversation admitted is an
  `unreadable-transcript` error on EVERY door, never "too short".
"""

import json
import os
import time

import pytest

from daimon_briefing import (capture, cli, heal_host, hooks, importer, ledger,
                             serializer, store, transcript)
from tests.conftest import FIXTURES, make_messages

PROJECT = "/p/unreadable"


def _row(**kw):
    return json.dumps(kw)


def _jsonl(*rows):
    return "\n".join(rows) + "\n"


def _boom_chat(*_a, **_k):
    raise AssertionError("the model must not be called for an unreadable file")


def _log(tmp_path):
    p = tmp_path / ".daimon" / "logs" / "serialize.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _nonempty_lines(text):
    return sum(1 for ln in text.splitlines() if ln.strip())


# ---- the account: every non-empty line lands in exactly one bucket -----------

_KIMI = _jsonl(
    _row(type="metadata", protocol_version="1"),
    _row(type="context.append_message",
         message={"role": "user", "content": [{"type": "text", "text": "hi"}]}),
    _row(type="prompt.accepted", input=[{"type": "text", "text": "hi"}]),
    _row(type="context.append_loop_event",
         event={"type": "content.part", "part": {"type": "text", "text": "yo"}}),
    _row(type="context.append_loop_event",
         event={"type": "tool.call", "name": "bash", "toolCallId": "c1",
                "args": {}}),
    _row(type="context.append_loop_event",
         event={"type": "tool.result", "toolCallId": "c1",
                "result": {"output": "ok"}}),
    _row(type="context.append_loop_event",
         event={"type": "tool.result", "result": {"output": "no id"}}),
    _row(type="something.new"),
    "not json at all",
)
_CODEX_EVENTS = _jsonl(
    _row(type="session_meta", payload={"cwd": "/x"}),
    _row(type="event_msg",
         payload={"type": "user_message", "message": "hello"}),
    _row(type="event_msg",
         payload={"type": "agent_message", "message": "  "}),
    _row(type="response_item", payload={"type": "message"}),
    _row(type="event_msg", payload={"type": "token_count"}),
)
_CODEX_ITEMS = _jsonl(
    _row(type="event_msg", payload={
        "type": "item_completed",
        "item": {"type": "UserMessage",
                 "content": [{"type": "text", "text": "hello"}]}}),
    _row(type="event_msg", payload={
        "type": "item_completed",
        "item": {"type": "Reasoning", "content": []}}),
)
_WINDSURF = _jsonl(
    _row(type="user_input", status="done",
         user_input={"user_response": "hello"}),
    _row(type="planner_response", status="canceled",
         planner_response={"response": "x"}),
    _row(type="planner_response", status="done",
         planner_response={"response": ""}),
    _row(type="grep_search_v2", status="done"),
)
_CLAUDE = _jsonl(
    _row(type="user", uuid="u1", message={"role": "user", "content": "hi"}),
    _row(type="assistant", uuid="u2", isSidechain=True,
         message={"role": "assistant", "content": "sub"}),
    _row(type="attachment", uuid="u3", attachment={"type": "x"}),
    _row(type="file-history-snapshot", snapshot={}),
    _row(type="a-row-type-nobody-named-yet", payload={}),
    _row(type="assistant", uuid="u4",
         message={"role": "assistant",
                  "content": [{"type": "tool_use", "id": "t", "name": "b",
                               "input": {}}]}),
    _row(type="user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}),
    _row(type="user", uuid="u6", message={"role": "user", "content": ""}),
    "{broken",
    "[1, 2]",
)
_MARKDOWN = "preamble line\n**user**: hi\nmore\n**assistant**:\n\n**user**: bye\n"

_ADAPTER_SAMPLES = {
    "kimi": ("wire.jsonl", _KIMI),
    "codex-events": ("rollout-events.jsonl", _CODEX_EVENTS),
    "codex-items": ("rollout-items.jsonl", _CODEX_ITEMS),
    "windsurf": ("cascade.jsonl", _WINDSURF),
    "claude": ("claude.jsonl", _CLAUDE),
    "markdown": ("chat.md", _MARKDOWN),
    "plain": ("chat.txt", "one\n\ntwo\n"),
}


def _kimi_path(tmp_path):
    d = tmp_path / "agents" / "a1"
    d.mkdir(parents=True)
    return d / "wire.jsonl"


def _fixture_cases(tmp_path):
    cases = []
    for p in sorted(FIXTURES.iterdir()):
        cases.append((p.name, p.read_text(encoding="utf-8"), p))
    for name, (fname, text) in _ADAPTER_SAMPLES.items():
        path = _kimi_path(tmp_path) if name == "kimi" else tmp_path / fname
        path.write_text(text, encoding="utf-8")
        cases.append((name, text, path))
    return cases


def test_account_sums_to_the_non_empty_lines_for_every_fixture_and_adapter(
        tmp_path):
    for name, text, path in _fixture_cases(tmp_path):
        account = transcript.from_file_detailed(path)["account"]
        assert account.rows == _nonempty_lines(text), name
        assert (account.admitted + sum(account.excluded.values())
                + sum(account.dropped.values())) == account.rows, name
        assert set(account.excluded) <= set(transcript.EXCLUDED_ROWS), name
        assert set(account.dropped) <= set(transcript.DROP_REASONS), name


def test_claude_rows_are_bucketed_by_reason(tmp_path):
    p = tmp_path / "claude.jsonl"
    p.write_text(_CLAUDE, encoding="utf-8")
    account = transcript.from_file_detailed(p)["account"]
    assert account.admitted == 1
    assert dict(account.excluded) == {
        "sidechain": 1, "attachment": 1, "file-history-snapshot": 1}
    assert dict(account.dropped) == {
        "no-role": 1, "tool-use-only": 1, "tool-result-without-id": 1,
        "no-content": 1, "unparsable-line": 2}


def test_every_adapter_reports_its_drops_with_the_shared_codes(tmp_path):
    expect = {
        "kimi": {"no-role": 3, "tool-use-only": 1,
                 "tool-result-without-id": 1, "unparsable-line": 1},
        "codex-events": {"no-role": 3, "no-content": 1},
        "codex-items": {"no-role": 1},
        "windsurf": {"no-content": 2, "no-role": 1},
        "markdown": {"no-role": 1, "no-content": 1},
    }
    cases = {n: p for n, _t, p in _fixture_cases(tmp_path)}
    for name, dropped in expect.items():
        account = transcript.from_file_detailed(cases[name])["account"]
        assert dict(account.dropped) == dropped, name


def test_kimi_rows_with_empty_text_are_dropped_as_no_content(tmp_path):
    p = _kimi_path(tmp_path)
    p.write_text(_jsonl(
        _row(type="metadata", protocol_version="1"),
        _row(type="prompt.accepted", input=[]),
        _row(type="context.append_loop_event",
             event={"type": "content.part",
                    "part": {"type": "text", "text": "  "}}),
    ), encoding="utf-8")
    account = transcript.from_file_detailed(p)["account"]
    assert dict(account.dropped) == {"no-role": 1, "no-content": 2}
    assert account.admitted == 0


def test_the_messages_only_reader_agrees_with_the_accounted_one(tmp_path):
    text = _CLAUDE
    assert transcript._from_jsonl(text) == transcript._from_jsonl_accounted(
        text)[0]


def test_hook_door_skips_a_session_store_that_answered_empty(
        tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(transcript, "from_session_detailed",
                        lambda s: transcript.SessionRead([], "empty"))
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-store", completed=True,
                         interrupted=False, model="m", platform="cli")
    logged = _log(tmp_path)
    assert "skipped serialize for S-store" in logged
    assert "empty-transcript" in logged and "error:" not in logged


def test_plain_text_and_markdown_blob_are_fully_admitted(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("one\n\ntwo\n", encoding="utf-8")
    account = transcript.from_file_detailed(p)["account"]
    assert (account.rows, account.admitted, dict(account.dropped)) == (2, 2, {})


# ---- the notes: one per reason, a stable code ---------------------------------


def test_a_plain_role_content_file_notes_the_tool_result_it_dropped():
    detailed = transcript.from_file_detailed(
        FIXTURES / "generic_tool_result_without_id.jsonl")
    assert "rows-dropped: tool-result-without-id=1" in detailed["notes"]
    assert "rows-dropped: tool-use-only=1" in detailed["notes"]
    assert len(detailed["messages"]) == 3  # shape unchanged: still dropped


def test_notes_travel_through_admitted_messages_without_changing_its_shape():
    got = transcript.admitted_messages(
        FIXTURES / "generic_tool_result_without_id.jsonl")
    assert "rows-dropped: tool-result-without-id=1" in got.notes
    assert len(got.messages) == 3


def test_a_clean_transcript_carries_no_drop_note(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_jsonl(_row(role="user", content="a"),
                        _row(role="assistant", content="b")), encoding="utf-8")
    assert transcript.from_file_detailed(p)["notes"] == []


# ---- the decision: one gate, every door ----------------------------------------


@pytest.mark.parametrize("name", ["generic_unrecognized_rows.jsonl",
                                  "generic_unknown_roles.jsonl"])
def test_cli_door_fails_an_unreadable_file_and_never_calls_it_too_short(
        name, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    rc = cli._run_serialize(FIXTURES / name, PROJECT)
    out = capsys.readouterr()
    assert rc == 1
    text = out.out + out.err
    assert "error: unreadable-transcript" in text
    assert "too short" not in text
    logged = _log(tmp_path)
    assert "error: unreadable-transcript" in logged
    assert "too short" not in logged
    # The line `status` counts: it parses as an error result.
    assert ledger._RESULT_ERR_RE.match(
        [ln for ln in logged.splitlines() if ln.startswith("error:")][0])


@pytest.mark.parametrize("name", ["generic_unrecognized_rows.jsonl",
                                  "generic_unknown_roles.jsonl"])
def test_hook_door_ledgers_an_unreadable_file_as_a_healable_failure(
        name, tmp_path, monkeypatch):
    sid = "S-unreadable"
    path = tmp_path / f"{sid}.jsonl"
    path.write_text((FIXTURES / name).read_text(encoding="utf-8"),
                    encoding="utf-8")
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id=sid, completed=True, interrupted=False,
                         model="m", platform="cli",
                         transcript_path=str(path))
    logged = _log(tmp_path)
    assert "error: unreadable-transcript" in logged
    assert "too short" not in logged
    plan = heal_host.heal_plan(sessions=[sid])
    assert [t.session_id for t in plan.targets] == [sid]


def test_cli_door_failure_is_a_heal_target(tmp_path, monkeypatch):
    sid = "S-cli"
    path = tmp_path / f"{sid}.jsonl"
    path.write_text((FIXTURES / "generic_unrecognized_rows.jsonl").read_text(
        encoding="utf-8"), encoding="utf-8")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ledger._append_serialize_log(
        f"{stamp} session-end: spawned serialize for {sid} "
        f"(reason: exit, project: {PROJECT}) (transcript: {path})")
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    assert cli._run_serialize(path, PROJECT) == 1
    plan = heal_host.heal_plan(sessions=[sid])
    assert [t.session_id for t in plan.targets] == [sid]


def test_import_door_names_the_unreadable_file_in_the_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    root = tmp_path / "projects"
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(root))
    (root / store.project_slug(PROJECT)).mkdir(parents=True)
    path = root / store.project_slug(PROJECT) / "S-imp.jsonl"
    path.write_text((FIXTURES / "generic_unknown_roles.jsonl").read_text(
        encoding="utf-8"), encoding="utf-8")
    os.utime(path, (1_782_000_000, 1_782_000_000))
    plan = importer.plan_import(PROJECT, [path], now=time.time(), log_text="")
    assert plan.sessions == ()
    (skip,) = plan.skipped
    assert skip.reason.startswith("unreadable-transcript")
    assert "too short" not in skip.reason


def test_a_zero_row_file_is_an_empty_transcript_skip_on_the_cli_door(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    rc = cli._run_serialize(FIXTURES / "empty_transcript.jsonl", PROJECT)
    assert rc == 0
    out = capsys.readouterr().out
    assert "skipped serialize" in out and "empty-transcript" in out
    assert "too short" not in out
    assert "error:" not in _log(tmp_path)


def test_a_zero_row_file_is_an_empty_transcript_skip_on_the_hook_door(
        tmp_path, monkeypatch):
    path = tmp_path / "S-empty.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-empty", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(path))
    logged = _log(tmp_path)
    assert "skipped serialize for S-empty" in logged
    assert "empty-transcript" in logged
    assert "too short" not in logged and "error:" not in logged


def test_hook_door_still_records_a_genuine_short_session_once(
        tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "from_session",
                        lambda s: make_messages(3))
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-short", completed=True,
                         interrupted=False, model="m", platform="cli")
    logged = _log(tmp_path)
    assert logged.count("skipped serialize for S-short") == 1
    assert "transcript too short (3 < 10 messages)" in logged


def test_hook_door_without_any_source_is_a_failure_not_silence(
        tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-none", completed=True,
                         interrupted=False, model="m", platform="cli")
    assert "error: unreadable-transcript" in _log(tmp_path)


# ---- capture.run and the serializer gate ---------------------------------------


def _account(**kw):
    base = dict(rows=0, admitted=0, excluded={}, dropped={})
    base.update(kw)
    return transcript.RowAccount(**base)


def test_capture_run_decides_before_the_too_short_gate():
    with pytest.raises(serializer.UnreadableTranscript):
        capture.run("S", [], project=PROJECT, chat=_boom_chat, deadline=1e12,
                    account=_account(rows=4, dropped={"no-role": 4}))
    with pytest.raises(serializer.EmptyTranscript):
        capture.run("S", [], project=PROJECT, chat=_boom_chat, deadline=1e12,
                    account=_account())
    with pytest.raises(serializer.TooShortError) as short:
        capture.run("S", make_messages(2), project=PROJECT, chat=_boom_chat,
                    deadline=1e12,
                    account=_account(rows=2, admitted=2))
    assert not isinstance(short.value, (serializer.UnreadableTranscript,
                                        serializer.EmptyTranscript))


def test_unreadable_is_a_serialize_error_and_empty_is_a_skip():
    assert issubclass(serializer.UnreadableTranscript, serializer.SerializeError)
    assert not issubclass(serializer.UnreadableTranscript,
                          serializer.TooShortError)
    assert issubclass(serializer.EmptyTranscript, serializer.TooShortError)


# ---- from_session: unavailable is not empty ------------------------------------


def test_from_session_detailed_tells_unavailable_from_empty(monkeypatch):
    monkeypatch.setattr(transcript, "_load_session_db", lambda: None)
    assert transcript.from_session_detailed("s").status == "unavailable"

    class Db:
        def __init__(self, result):
            self.result = result

        def get_messages_as_conversation(self, sid):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    monkeypatch.setattr(transcript, "_load_session_db", lambda: Db([]))
    got = transcript.from_session_detailed("s")
    assert (got.status, got.messages) == ("empty", [])
    monkeypatch.setattr(transcript, "_load_session_db",
                        lambda: Db(RuntimeError("x")))
    assert transcript.from_session_detailed("s").status == "failed"
    monkeypatch.setattr(transcript, "_load_session_db",
                        lambda: Db(make_messages(2)))
    got = transcript.from_session_detailed("s")
    assert got.status == "ok" and len(got.messages) == 2
    assert transcript.from_session("s") == got.messages


# ---- bookkeeping rows: named exclusions, and a skip when nothing else is there --

BOOKKEEPING = (
    "session-owner", "file-history-snapshot", "file-history-delta",
    "last-prompt", "ai-title", "mode", "permission-mode", "queue-operation",
    "system", "summary", "pr-link", "bridge-session", "cost-state",
    "tools-used", "atis-latch", "api-request", "api-request-blob",
    "api-request-shape",
)


@pytest.mark.parametrize("kind", BOOKKEEPING)
def test_a_known_bookkeeping_row_type_is_a_named_exclusion(kind):
    assert transcript.excluded_reason({"type": kind}) == kind
    assert kind in transcript.EXCLUDED_ROWS


def test_a_bookkeeping_type_never_hides_a_row_that_carries_a_role():
    row = {"type": "system", "message": {"role": "user", "content": "hi"}}
    assert transcript.excluded_reason(row) is None
    assert transcript.excluded_reason({"type": ["unhashable"]}) is None
    assert transcript.excluded_reason({"type": "something-new"}) is None


def test_an_exclusion_that_sits_between_turns_keeps_coverage_available(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_jsonl(
        _row(type="user", uuid="u1", message={"role": "user", "content": "a"}),
        _row(type="session-owner", sessionId="x"),
        _row(type="assistant", uuid="u2",
             message={"role": "assistant", "content": "b"})), encoding="utf-8")
    detailed = transcript.from_file_detailed(p)
    assert detailed["coverage"]["available"] is True
    assert dict(detailed["account"].excluded) == {"session-owner": 1}


def test_require_readable_skips_only_when_every_row_was_excluded():
    only = _account(rows=2, excluded={"session-owner": 2})
    with pytest.raises(serializer.NoConversation) as skip:
        serializer.require_readable([], only)
    assert "no-conversation" in str(skip.value)
    assert isinstance(skip.value, serializer.TooShortError)
    mixed = _account(rows=3, excluded={"session-owner": 2},
                     dropped={"no-role": 1})
    with pytest.raises(serializer.UnreadableTranscript):
        serializer.require_readable([], mixed)
    tools = [{"role": "tool", "content": "x", "id": "t", "tool_result": True}]
    with pytest.raises(serializer.UnreadableTranscript):
        serializer.require_readable(tools, _account(rows=1, admitted=1))


def test_cli_door_skips_a_file_of_bookkeeping_rows_only(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    rc = cli._run_serialize(FIXTURES / "claude_session_owner_only.jsonl",
                            PROJECT)
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped serialize" in out and "no-conversation" in out
    assert "session-owner=2" in out
    assert "error:" not in out + _log(tmp_path)
    assert "too short" not in out


def test_hook_door_skips_a_file_of_bookkeeping_rows_only(tmp_path, monkeypatch):
    path = tmp_path / "S-owner.jsonl"
    path.write_text((FIXTURES / "claude_session_owner_only.jsonl").read_text(
        encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-owner", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(path))
    logged = _log(tmp_path)
    assert "skipped serialize for S-owner" in logged
    assert "no-conversation" in logged
    assert "error:" not in logged and "too short" not in logged


def test_one_unknown_row_among_bookkeeping_rows_is_still_a_failure(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    rc = cli._run_serialize(
        FIXTURES / "claude_session_owner_with_unknown_row.jsonl", PROJECT)
    out = capsys.readouterr()
    assert rc == 1
    assert "error: unreadable-transcript" in out.out + out.err
    path = tmp_path / "S-mixed.jsonl"
    path.write_text((FIXTURES / "claude_session_owner_with_unknown_row.jsonl"
                     ).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(transcript, "from_session", lambda s: [])
    monkeypatch.setattr(hooks, "_chat", _boom_chat)
    hooks.on_session_end(session_id="S-mixed", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(path))
    assert "error: unreadable-transcript" in _log(tmp_path)


def test_import_plan_names_a_bookkeeping_only_file_as_no_conversation(
        tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    root = tmp_path / "projects"
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(root))
    (root / store.project_slug(PROJECT)).mkdir(parents=True)
    path = root / store.project_slug(PROJECT) / "S-imp.jsonl"
    path.write_text((FIXTURES / "claude_session_owner_only.jsonl").read_text(
        encoding="utf-8"), encoding="utf-8")
    os.utime(path, (1_782_000_000, 1_782_000_000))
    plan = importer.plan_import(PROJECT, [path], now=time.time(), log_text="")
    (skip,) = plan.skipped
    assert skip.reason.startswith("no-conversation")
