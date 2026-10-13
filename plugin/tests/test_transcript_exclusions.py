"""The rows a capture never reads (#1178).

One predicate, `transcript.excluded_reason`, decides. The message loop and the
coverage sidecar both call it, so they cannot disagree about a row.
"""

import json
from pathlib import Path

import pytest

from daimon_briefing import transcript

FIXTURES = Path(__file__).parent / "fixtures"
MIXED = FIXTURES / "claude_attachment_rows.jsonl"
SUMMARY_FIRST = FIXTURES / "claude_summary_first.jsonl"

INJECTED = ("SYNTH-INJECT-CONTEXT", "SYNTH-INJECT-SUCCESS",
            "SYNTH-INJECT-WITHMSG", "SYNTH-SUMMARY-BODY",
            "SYNTH-SIDECHAIN-TEXT", "SYNTH-META-TEXT")


@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here.
    return tmp_path / ".daimon" / "logs"


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


# --- the predicate -------------------------------------------------------

@pytest.mark.parametrize("row, code", [
    ({"type": "user", "isSidechain": True}, "sidechain"),
    ({"type": "user", "isMeta": True}, "meta"),
    ({"type": "attachment"}, "attachment"),
    ({"type": "user", "isCompactSummary": True}, "compact-summary"),
])
def test_each_rule_names_its_row(row, code):
    assert transcript.excluded_reason(row) == code


def test_a_conversation_row_is_not_excluded():
    assert transcript.excluded_reason(
        {"type": "user", "message": {"role": "user", "content": "hi"}}) is None
    assert transcript.excluded_reason({"type": "user",
                                       "isCompactSummary": False}) is None


def test_the_check_order_is_sidechain_meta_attachment_summary():
    everything = {"type": "attachment", "isSidechain": True, "isMeta": True,
                  "isCompactSummary": True}
    assert transcript.excluded_reason(everything) == "sidechain"
    del everything["isSidechain"]
    assert transcript.excluded_reason(everything) == "meta"
    del everything["isMeta"]
    assert transcript.excluded_reason(everything) == "attachment"
    everything["type"] = "user"
    assert transcript.excluded_reason(everything) == "compact-summary"


def test_the_codes_are_exactly_the_four_in_order():
    assert transcript.EXCLUDED_CODES == (
        "sidechain", "meta", "attachment", "compact-summary")


# --- the loader ----------------------------------------------------------

def test_the_loader_admits_only_the_conversation():
    detailed = transcript.from_file_detailed(MIXED)
    messages = detailed["messages"]
    assert [m["id"] for m in messages] == [
        "00000000-0000-4000-8000-0000000000b1",
        "00000000-0000-4000-8000-0000000000b2",
        "00000000-0000-4000-8000-0000000000b3",
        "00000000-0000-4000-8000-0000000000b4",
    ]
    blob = json.dumps(messages)
    for marker in INJECTED:
        assert marker not in blob


def test_an_attachment_row_with_a_message_dict_is_still_excluded(tmp_path):
    path = _jsonl(tmp_path / "t.jsonl", [
        {"type": "user", "uuid": "u1",
         "message": {"role": "user", "content": "real question"}},
        {"type": "attachment", "uuid": "a1",
         "attachment": {"type": "queued_command"},
         "message": {"role": "user", "content": "SYNTH-INJECT-WITHMSG"}},
        {"type": "assistant", "uuid": "u2",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "real answer"}]}},
    ])
    detailed = transcript.from_file_detailed(path)
    assert [m["id"] for m in detailed["messages"]] == ["u1", "u2"]
    assert detailed["coverage"]["available"] is True


def test_the_generic_loader_excludes_attachment_rows_too(tmp_path):
    path = _jsonl(tmp_path / "generic.jsonl", [
        {"role": "user", "content": "plain question"},
        {"type": "attachment", "role": "user",
         "content": "SYNTH-INJECT-GENERIC"},
        {"role": "assistant", "content": "plain answer"},
        {"role": "user", "content": "summary", "isCompactSummary": True},
    ])
    messages = transcript.from_file(path)
    assert [m["content"] for m in messages] == ["plain question",
                                                "plain answer"]


# --- coverage shares the predicate --------------------------------------

def test_coverage_stays_available_and_matches_the_messages():
    detailed = transcript.from_file_detailed(MIXED)
    coverage = detailed["coverage"]
    assert coverage["available"] is True
    assert coverage["reason"] is None
    assert [r["id"] for r in coverage["rows"]] == [
        m["id"] for m in detailed["messages"]]


def test_no_attachment_or_summary_becomes_a_host_user_input_row():
    ids = {r["id"] for r in transcript.from_file_detailed(MIXED)
           ["coverage"]["rows"]}
    for excluded in ("a1", "a2", "a3", "c1", "d1", "d2"):
        assert f"00000000-0000-4000-8000-0000000000{excluded}" not in ids


def test_dropping_the_summary_from_one_loop_only_would_flip_coverage(
        monkeypatch):
    """The trap the shared predicate prevents (a summary row is type user)."""
    original = transcript.excluded_reason

    def loop_only(obj):
        # The coverage sidecar would not know the summary rule.
        import inspect
        caller = inspect.stack()[1].function
        if caller == "_claude_coverage" and obj.get("isCompactSummary"):
            return None
        return original(obj)

    monkeypatch.setattr(transcript, "excluded_reason", loop_only)
    coverage = transcript.from_file_detailed(MIXED)["coverage"]
    assert coverage["available"] is False
    assert coverage["reason"] == "coverage_incomplete"


# --- the summary-first note ---------------------------------------------

def test_a_summary_that_opens_the_file_leaves_a_note():
    detailed = transcript.from_file_detailed(SUMMARY_FIRST)
    assert detailed["notes"] == ["compact-summary-only-context"]
    assert [m["content"] for m in detailed["messages"]] == [
        "Continue with the docs page.", "The docs page is drafted."]
    assert detailed["coverage"]["available"] is True


def test_a_summary_after_the_conversation_leaves_no_note():
    assert transcript.from_file_detailed(MIXED)["notes"] == []


def test_a_file_holding_only_a_summary_still_says_so(tmp_path):
    path = _jsonl(tmp_path / "only.jsonl", [
        {"type": "user", "uuid": "c1", "isCompactSummary": True,
         "message": {"role": "user", "content": "SYNTH-SUMMARY-BODY"}},
    ])
    detailed = transcript.from_file_detailed(path)
    assert detailed["messages"] == []
    assert detailed["notes"] == ["compact-summary-only-context"]


def test_a_file_with_no_conversation_rows_leaves_no_note(tmp_path):
    path = _jsonl(tmp_path / "noise.jsonl", [
        {"type": "attachment", "uuid": "a1"},
        {"type": "mode", "mode": "normal"},
    ])
    detailed = transcript.from_file_detailed(path)
    assert detailed["messages"] == []
    assert detailed["notes"] == []


def test_only_the_generic_branch_computes_notes(tmp_path):
    md = tmp_path / "t.md"
    md.write_text("user: hello there\nassistant: hi\n", encoding="utf-8")
    assert transcript.from_file_detailed(md)["notes"] == []
    codex = _jsonl(tmp_path / "c.jsonl", [
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "hello"}}])
    assert transcript.from_file_detailed(codex)["notes"] == []


# --- the serialize log carries the note ---------------------------------

def test_the_cli_door_logs_the_summary_first_note(
        tmp_log_dir, tmp_path, fake_chat_factory, monkeypatch):
    from daimon_briefing import cli

    monkeypatch.setattr(cli, "_chat", fake_chat_factory("{}"))

    path = tmp_path / "S-sum.jsonl"
    path.write_text(SUMMARY_FIRST.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert cli._run_serialize(path, "/p") == 0   # too short: a skip, not a run
    log = (tmp_log_dir / "serialize.log").read_text(encoding="utf-8")
    assert "note for S-sum: compact-summary-only-context" in log
    assert "error:" not in log


def test_the_cli_door_logs_no_note_for_an_ordinary_file(
        tmp_log_dir, tmp_path, fake_chat_factory, monkeypatch):
    from daimon_briefing import cli

    monkeypatch.setattr(cli, "_chat", fake_chat_factory("{}"))

    path = tmp_path / "S-plain.jsonl"
    path.write_text(MIXED.read_text(encoding="utf-8"), encoding="utf-8")
    cli._run_serialize(path, "/p")
    log = (tmp_log_dir / "serialize.log").read_text(encoding="utf-8")
    assert "note for" not in log


def test_the_hook_door_logs_the_summary_first_note(
        _isolate_daimon_home, tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import hooks, transcript

    monkeypatch.setattr(transcript, "from_session", lambda sid: [])
    hooks.on_session_end(session_id="S-hook", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(SUMMARY_FIRST))
    log = _isolate_daimon_home / "logs" / "serialize.log"
    assert ("note for S-hook: compact-summary-only-context"
            in log.read_text(encoding="utf-8"))


# --- the first-row edge cases of the summary-first note ------------------

def _user(uuid, content):
    return {"type": "user", "uuid": uuid,
            "message": {"role": "user", "content": content}}


def _summary(uuid="c1"):
    return {"type": "user", "uuid": uuid, "isCompactSummary": True,
            "message": {"role": "user", "content": "SYNTH-SUMMARY-BODY"}}


@pytest.mark.parametrize("before", [
    {"type": "user", "uuid": "s", "isSidechain": True,
     "message": {"role": "user", "content": "SYNTH-SIDECHAIN-TEXT"}},
    {"type": "user", "uuid": "m", "isMeta": True,
     "message": {"role": "user", "content": "SYNTH-META-TEXT"}},
    {"type": "attachment", "uuid": "a",
     "attachment": {"type": "hook_success"}},
    _user("e", ""),
    {"type": "user",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
], ids=["sidechain", "meta", "attachment", "empty-text", "tool-result-no-id"])
def test_rows_the_loader_drops_do_not_hide_a_summary_first_file(
        tmp_path, before):
    path = _jsonl(tmp_path / "t.jsonl", [before, _summary(),
                                         _user("u1", "after the summary")])
    detailed = transcript.from_file_detailed(path)
    assert [m["content"] for m in detailed["messages"]] == [
        "after the summary"]
    assert detailed["notes"] == ["compact-summary-only-context"]


def test_a_tool_result_message_before_the_summary_is_earlier_context(
        tmp_path):
    tool_row = {"type": "user", "uuid": "t1", "message": {
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}
    path = _jsonl(tmp_path / "t.jsonl", [tool_row, _summary()])
    detailed = transcript.from_file_detailed(path)
    assert [m["id"] for m in detailed["messages"]] == ["t1"]
    assert detailed["notes"] == []


def test_a_summary_row_with_no_role_is_not_a_conversation_row(tmp_path):
    path = _jsonl(tmp_path / "t.jsonl", [
        {"type": "system", "uuid": "c1", "isCompactSummary": True,
         "content": "SYNTH-SUMMARY-BODY"},
        _user("u1", "hello there")])
    assert transcript.from_file_detailed(path)["notes"] == []


# --- the other hosts are untouched --------------------------------------

def test_kimi_wire_rows_are_not_read_by_the_generic_predicate(tmp_path):
    from tests.test_kimi_transcript import _append_message, _metadata

    flagged = _append_message("kimi prompt text")
    flagged.update({"isSidechain": True, "isMeta": True,
                    "isCompactSummary": True})
    path = _jsonl(tmp_path / "wire.jsonl", [_metadata(), flagged])
    detailed = transcript.from_file_detailed(path)
    assert [m["content"] for m in detailed["messages"]] == ["kimi prompt text"]
    assert detailed["coverage"]["reason"] == "source_binding_unavailable"
    assert detailed["notes"] == []


def test_windsurf_rows_are_not_read_by_the_generic_predicate(tmp_path):
    path = _jsonl(tmp_path / "cascade.jsonl", [
        {"type": "user_input", "status": "done", "isCompactSummary": True,
         "user_input": {"user_response": "windsurf prompt"}},
        {"type": "planner_response", "status": "done", "isSidechain": True,
         "planner_response": {"response": "windsurf answer"}},
    ])
    detailed = transcript.from_file_detailed(path)
    assert [m["content"] for m in detailed["messages"]] == [
        "windsurf prompt", "windsurf answer"]
    assert detailed["coverage"]["reason"] == "unsupported_adapter"
    assert detailed["notes"] == []


# --- the log note is invisible to status, heal and stats ----------------

def test_note_lines_change_nothing_status_heal_or_stats_read(
        tmp_log_dir, tmp_path, tmp_checkpoint_dir):
    from datetime import datetime, timezone

    from daimon_briefing import ledger

    transcript_file = tmp_path / "S1.jsonl"
    transcript_file.write_text("{}\n", encoding="utf-8")
    base = [
        f"2026-07-10T11:00:00Z session-end: spawned serialize for S1 "
        f"(project: /p) (transcript: {transcript_file})",
        f"error: boom (transcript: {transcript_file}) after 3s",
        "wrote checkpoint: /c/S2.json (took 5s)",
    ]
    notes = [
        "note for S1: compact-summary-only-context",
        "note for error: x: compact-summary-only-context",
        "note for wrote checkpoint: a (took 1s): compact-summary-only-context",
        "note for S1 (transcript: /t/x.jsonl): compact-summary-only-context",
    ]
    when = datetime(2026, 7, 10, 11, 1, tzinfo=timezone.utc)
    now = when.timestamp()

    def fold(lines):
        text = "\n".join(lines) + "\n"
        log = tmp_log_dir / "serialize.log"
        tmp_log_dir.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
        return (ledger._session_ledger(text, now),
                ledger._parse_serialize_log(log, now),
                ledger._stats_capture(when),
                ledger._compute_outstanding(text, now),
                ledger._heal_plan(text, now))

    plain = fold(base)
    with_notes = fold(base[:1] + notes[:2] + base[1:2] + notes[2:] + base[2:])
    assert plain[4]["target"] is not None   # the fixture really is healable
    assert with_notes[0] == plain[0]
    assert with_notes[2] == plain[2]
    assert with_notes[3] == plain[3]
    assert with_notes[4] == plain[4]
    # The log parser reports the last result line; the notes never become one.
    assert with_notes[1]["result"] == plain[1]["result"]
    assert with_notes[1]["spawn"] == plain[1]["spawn"]


def test_the_hook_door_logs_the_note_when_the_file_is_the_source(
        _isolate_daimon_home, tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import hooks

    file_messages = transcript.from_file_detailed(SUMMARY_FIRST)["messages"]
    monkeypatch.setattr(transcript, "from_session",
                        lambda sid: list(file_messages))
    hooks.on_session_end(session_id="S-same", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(SUMMARY_FIRST))
    log = _isolate_daimon_home / "logs" / "serialize.log"
    assert ("note for S-same: compact-summary-only-context"
            in log.read_text(encoding="utf-8"))


def test_the_hook_door_logs_no_note_when_the_session_db_is_the_source(
        _isolate_daimon_home, tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import hooks
    from tests.conftest import make_messages

    monkeypatch.setattr(transcript, "from_session",
                        lambda sid: make_messages(3))
    hooks.on_session_end(session_id="S-db", completed=True,
                         interrupted=False, model="m", platform="cli",
                         transcript_path=str(SUMMARY_FIRST))
    log = _isolate_daimon_home / "logs" / "serialize.log"
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "note for" not in text


# --- the public surface -------------------------------------------------

def test_excluded_rows_names_each_code_and_is_read_only():
    from daimon_briefing import api

    assert set(api.EXCLUDED_ROWS) == set(transcript.EXCLUDED_CODES)
    assert tuple(api.EXCLUDED_ROWS) == transcript.EXCLUDED_CODES
    assert all(isinstance(v, str) and v and "\n" not in v
               for v in api.EXCLUDED_ROWS.values())
    with pytest.raises(TypeError):
        api.EXCLUDED_ROWS["extra"] = "no"  # type: ignore[index]


def test_admitted_messages_returns_exactly_what_capture_reads():
    from daimon_briefing import api

    got = api.admitted_messages(MIXED)
    assert isinstance(got, api.Admitted)
    assert [dict(m) for m in got.messages] == \
        transcript.from_file_detailed(MIXED)["messages"]
    assert got.notes == ()
    blob = json.dumps([dict(m) for m in got.messages])
    for marker in INJECTED:
        assert marker not in blob


def test_admitted_messages_are_read_only():
    from daimon_briefing import api

    got = api.admitted_messages(MIXED)
    assert isinstance(got.messages, tuple)
    with pytest.raises(TypeError):
        got.messages[0]["content"] = "changed"  # type: ignore[index]


def test_admitted_messages_carries_the_summary_first_note():
    from daimon_briefing import api

    got = api.admitted_messages(SUMMARY_FIRST)
    assert "compact-summary-only-context" in got.notes
    assert len(got.messages) == 2


def test_admitted_messages_names_a_coverage_that_is_unavailable(tmp_path):
    from daimon_briefing import api

    md = tmp_path / "t.md"
    md.write_text("user: hello there\nassistant: hi\n", encoding="utf-8")
    got = api.admitted_messages(md)
    assert got.notes == ("coverage-unavailable: unsupported_adapter",)
    assert got.messages


def test_admitted_messages_never_raises(tmp_path):
    from daimon_briefing import api

    missing = api.admitted_messages(tmp_path / "nope.jsonl")
    assert missing == api.Admitted((), ("transcript-unreadable",))
    binary = tmp_path / "bad.jsonl"
    binary.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert api.admitted_messages(binary).notes == ("transcript-unreadable",)
    assert api.admitted_messages(None).notes == ("transcript-unreadable",)
    assert api.admitted_messages(tmp_path).notes == ("transcript-unreadable",)
