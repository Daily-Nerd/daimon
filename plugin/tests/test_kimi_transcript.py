"""Kimi Code `wire.jsonl` folds into the same message list every other host
produces (#988).

Kimi does not write a message list. It writes an append-only EVENT log and
folds it into messages at read time, so the same user prompt appears in up to
three events (`prompt.accepted`, `turn.prompt`, `context.append_message`) and
one assistant turn arrives as a run of `content.part` fragments. A branch that
took every text-carrying event at face value would triple every prompt and
shatter every answer.

Every fixture here is SYNTHETIC, written by the helper below in the shapes
measured on a live 0.42.0 session. The real sample logs carry the
maintainer's session text and are never copied into this repo.
"""
import json

import pytest

from daimon_briefing import provenance, serializer, transcript

MS = 1788971099794  # epoch milliseconds, the unit Kimi stamps `time` in


def _metadata(ms=MS):
    return {"type": "metadata", "protocol_version": "1.0", "created_at": ms}


def _append_message(text, role="user", origin=None, ms=MS):
    return {
        "type": "context.append_message",
        "agentId": "main",
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
            "toolCalls": [],
            "origin": origin if origin is not None else {"kind": "user"},
        },
        "time": ms,
    }


def _turn_prompt(text, prompt_id="p-1", ms=MS):
    return {"type": "turn.prompt", "agentId": "main",
            "input": [{"type": "text", "text": text}],
            "origin": {"kind": "user"}, "promptId": prompt_id, "time": ms}


def _prompt_accepted(text, prompt_id="p-1", ms=MS):
    return {"type": "prompt.accepted", "agentId": "main",
            "promptId": prompt_id,
            "content": [{"type": "text", "text": text}], "time": ms}


def _part(kind, text, step_uuid="s-1", ms=MS):
    """A `content.part` loop event. `kind` is "text" (assistant prose) or
    "think" (the model's reasoning, which is noise for the serializer)."""
    return {"type": "context.append_loop_event", "agentId": "main",
            "event": {"type": "content.part", "uuid": "u-part",
                      "turnId": "1", "step": 1, "stepUuid": step_uuid,
                      "part": {"type": kind, kind: text}},
            "time": ms}


def _tool_call(name, args, call_id="call-1", ms=MS):
    return {"type": "context.append_loop_event", "agentId": "main",
            "event": {"type": "tool.call", "uuid": "u-call", "turnId": "1",
                      "step": 1, "stepUuid": "s-1", "toolCallId": call_id,
                      "name": name, "args": args,
                      "display": {"kind": "command"}},
            "time": ms}


def _tool_result(output, call_id="call-1", is_error=None, ms=MS):
    result = {"output": output}
    if is_error is not None:
        result["isError"] = is_error
    return {"type": "context.append_loop_event", "agentId": "main",
            "event": {"type": "tool.result", "parentUuid": "u-call",
                      "toolCallId": call_id, "result": result},
            "time": ms}


def _write(tmp_path, objs, name="wire.jsonl", agent="main"):
    """Write a wire log at the real on-disk shape:
    <sessions>/wd_<dir>_<hex>/session_<uuid>/agents/<agent>/wire.jsonl"""
    d = (tmp_path / "sessions" / "wd_proj_0123456789ab"
         / "session_abc" / "agents" / agent)
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n",
                    encoding="utf-8")
    return path


def _roles(msgs):
    return [(m["role"], m["content"]) for m in msgs]


# ---- detection ----

def test_metadata_first_line_selects_the_kimi_branch(tmp_path):
    path = _write(tmp_path, [_metadata(), _append_message("hello there")])
    assert _roles(transcript.from_file(path)) == [("user", "hello there")]


def test_wire_path_selects_the_branch_without_a_metadata_header(tmp_path):
    """A log rotated or truncated past its header is still a Kimi log: the
    path says so. Without this the file falls through to the generic branch,
    which reads `type` keys it was never meant to see."""
    path = _write(tmp_path, [_append_message("hello there")])
    assert _roles(transcript.from_file(path)) == [("user", "hello there")]


def test_a_claude_code_row_is_not_mistaken_for_kimi(tmp_path):
    path = tmp_path / "cc.jsonl"
    path.write_text(json.dumps(
        {"type": "user", "uuid": "u1",
         "message": {"role": "user", "content": "plain claude row"}}) + "\n")
    msgs = transcript.from_file(path)
    assert _roles(msgs) == [("user", "plain claude row")]
    assert msgs[0]["id"] == "u1"


# ---- the mirror problem ----

def test_one_prompt_carried_by_three_events_yields_one_message(tmp_path):
    """Measured on three live sessions: `prompt.accepted`, `turn.prompt` and
    `context.append_message` carry byte-identical text for the same prompt."""
    text = "run the suite and tell me what broke"
    path = _write(tmp_path, [
        _metadata(),
        _prompt_accepted(text),
        _turn_prompt(text),
        _append_message(text),
    ])
    assert _roles(transcript.from_file(path)) == [("user", text)]


def test_a_prompt_with_no_appended_message_is_still_captured_once(tmp_path):
    """A log cut off mid-turn keeps the lifecycle events and loses the
    canonical append. The prompt must survive, and survive exactly once."""
    text = "the prompt whose append never landed"
    path = _write(tmp_path, [
        _metadata(), _prompt_accepted(text), _turn_prompt(text)])
    assert _roles(transcript.from_file(path)) == [("user", text)]


def test_the_same_prompt_typed_twice_yields_two_messages(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _turn_prompt("one last one", "p-1"),
        _append_message("one last one"),
        _turn_prompt("one last one", "p-2"),
        _append_message("one last one"),
    ])
    assert _roles(transcript.from_file(path)) == [
        ("user", "one last one"), ("user", "one last one")]


# ---- assistant text and thinking ----

def test_text_parts_of_one_step_merge_into_one_assistant_message(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _append_message("question"),
        _part("text", "First half."),
        _part("text", "Second half."),
    ])
    assert _roles(transcript.from_file(path)) == [
        ("user", "question"), ("assistant", "First half.\nSecond half.")]


def test_thinking_parts_never_reach_the_message_list(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _part("think", "the model's private reasoning"),
        _part("text", "the answer"),
    ])
    assert _roles(transcript.from_file(path)) == [("assistant", "the answer")]


def test_a_tool_call_between_two_text_runs_splits_them(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _part("text", "Let me look."),
        _tool_call("Bash", {"command": "ls"}),
        _tool_result("a\nb"),
        _part("text", "Two files."),
    ])
    assert [m["role"] for m in transcript.from_file(path)] == [
        "assistant", "tool", "assistant"]


# ---- tool results ----

def test_tool_result_becomes_a_tool_message_keyed_on_the_call_id(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("Bash", {"command": "pytest -q"}, "call-9"),
        _tool_result("6059 passed", "call-9"),
    ])
    msg = transcript.from_file(path)[0]
    assert msg["role"] == "tool"
    assert msg["content"] == "6059 passed"
    assert msg["id"] == "call-9"
    assert msg["tool_result"] is True
    assert "tool_error" not in msg


def test_a_failed_tool_result_carries_tool_error(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("Bash", {"command": "false"}),
        _tool_result("exit 1", is_error=True),
    ])
    assert transcript.from_file(path)[0]["tool_error"] is True


def test_tool_output_is_capped_at_parse_time(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("Read", {"path": "big.txt"}),
        _tool_result("x" * 5000),
    ])
    content = transcript.from_file(path)[0]["content"]
    assert len(content) == transcript._TOOL_RESULT_MAX_CHARS


def test_empty_tool_output_still_reports_that_it_ran(tmp_path):
    path = _write(tmp_path, [
        _metadata(), _tool_call("Bash", {"command": "true"}), _tool_result("")])
    assert transcript.from_file(path)[0]["content"] == "(no output)"


def test_a_daimon_shell_call_marks_its_result_as_daimon_output(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("Bash", {"command": "daimon brief"}, "call-d"),
        _tool_result("DAIMON BRIEFING (checkpoint: S1)", "call-d"),
    ])
    assert transcript.from_file(path)[0]["daimon_output"] is True


def test_an_ordinary_shell_call_is_not_marked_daimon_output(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("Bash", {"command": "rg daimon plugin/"}, "call-r"),
        _tool_result("plugin/cli.py:1", "call-r"),
    ])
    assert "daimon_output" not in transcript.from_file(path)[0]


def test_a_daimon_mcp_tool_marks_its_result_as_daimon_output(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _tool_call("mcp__daimon__daimon_recall", {"query": "x"}, "call-m"),
        _tool_result("prior work", "call-m"),
    ])
    assert transcript.from_file(path)[0]["daimon_output"] is True


# ---- host scaffolding ----

def test_host_injected_reminders_are_dropped(tmp_path):
    """`origin.kind == "injection"` is Kimi's own scaffolding (a date change,
    a permission-mode notice). It is the direct analogue of Claude Code's
    `isMeta` rows, which this parser has always dropped."""
    path = _write(tmp_path, [
        _metadata(),
        _append_message("<system-reminder>\nToday's date is 2026-09-09.\n"
                        "</system-reminder>",
                        origin={"kind": "injection", "variant": "date_change"}),
        _append_message("real question"),
    ])
    assert _roles(transcript.from_file(path)) == [("user", "real question")]


def test_a_hook_result_message_survives_parsing(tmp_path):
    """Hook output is context the model actually read, so it stays in the
    message list. What it must never do is pass as a WITNESS."""
    briefing = ('<hook_result hook_event="UserPromptSubmit">\n'
                "DAIMON BRIEFING (checkpoint: S1, written 2m ago)\n"
                "we decided to keep the resolver\n</hook_result>")
    path = _write(tmp_path, [
        _metadata(),
        _append_message(briefing,
                        origin={"kind": "hook_result",
                                "event": "UserPromptSubmit"}),
        _append_message("and now my actual question"),
    ])
    msgs = transcript.from_file(path)
    assert [m["role"] for m in msgs] == ["user", "user"]
    assert "DAIMON BRIEFING" in msgs[0]["content"]


def test_the_injected_briefing_is_stripped_from_the_verification_haystack(
        tmp_path):
    """The reuse that matters: daimon's own briefing rides into a Kimi
    session inside a `hook_result` message of its own, so the existing
    message-scoped strip blanks it whole. A quote lifted from a PRIOR
    session's briefing must not verify as witnessed in THIS one (#440)."""
    briefing = ('<hook_result hook_event="UserPromptSubmit">\n'
                "DAIMON BRIEFING (checkpoint: S1, written 2m ago)\n"
                "we decided to keep the resolver\n</hook_result>")
    path = _write(tmp_path, [_metadata(), _append_message(
        briefing, origin={"kind": "hook_result", "event": "UserPromptSubmit"})])
    body = transcript.from_file(path)[0]["content"]
    assert "we decided to keep the resolver" not in serializer.strip_injected(body)


def test_a_skill_activation_prompt_is_kept_as_user_context(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _append_message("Skill instructions: read the docs first",
                        origin={"kind": "skill_activation",
                                "skillName": "check-docs"}),
    ])
    assert _roles(transcript.from_file(path)) == [
        ("user", "Skill instructions: read the docs first")]


# ---- robustness ----

@pytest.mark.parametrize("noise", [
    {"type": "llm.request", "model": "k2", "time": MS},
    {"type": "usage.record", "usage": {"output": 12}, "time": MS},
    {"type": "token_counting.measured", "tokens": 4, "time": MS},
    {"type": "permission.set_mode", "mode": "auto", "time": MS},
    {"type": "file_history.tracked", "path": "/x/y.py", "time": MS},
    {"type": "interaction.request", "request": {"toolName": "Bash"},
     "time": MS},
    {"type": "interaction.resolved", "response": {"decision": "approved"},
     "time": MS},
    {"type": "profile.bind", "systemPrompt": "a very long system prompt",
     "time": MS},
    {"type": "llm.tools_snapshot", "tools": [{"name": "Bash"}], "time": MS},
    {"type": "turn.ended", "reason": "completed", "time": MS},
    {"type": "an.event.type.that.does.not.exist.yet", "time": MS},
])
def test_noise_events_contribute_nothing_and_never_raise(tmp_path, noise):
    path = _write(tmp_path, [_metadata(), noise, _append_message("the ask")])
    assert _roles(transcript.from_file(path)) == [("user", "the ask")]


def test_a_malformed_line_is_skipped_not_fatal(tmp_path):
    path = _write(tmp_path, [_metadata(), _append_message("survivor")])
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n",
                    encoding="utf-8")
    assert _roles(transcript.from_file(path)) == [("user", "survivor")]


def test_a_header_only_log_yields_no_messages(tmp_path):
    """Never the raw-blob fallback: an empty session is empty, not one giant
    user message holding the whole event log."""
    path = _write(tmp_path, [_metadata(),
                             {"type": "runtime.set_binding", "time": MS}])
    assert transcript.from_file(path) == []


def test_a_subagent_log_parses_on_its_own_terms(tmp_path):
    """Subagent files are not merged into the main log in this slice. Handed
    one directly, the branch still folds it rather than refusing."""
    path = _write(tmp_path, [_metadata(), _append_message("subagent task")],
                  agent="sub-1")
    assert _roles(transcript.from_file(path)) == [("user", "subagent task")]


# ---- timestamps ----

def test_last_timestamp_reads_kimi_millisecond_stamps(tmp_path):
    path = _write(tmp_path, [
        _metadata(ms=MS),
        _append_message("first", ms=MS),
        _append_message("last", ms=MS + 15297),
    ])
    assert transcript.last_timestamp(path) == "2026-09-09T16:25:15Z"


def test_last_timestamp_takes_the_max_not_the_last_row(tmp_path):
    path = _write(tmp_path, [
        _metadata(ms=MS),
        _append_message("late", ms=MS + 60000),
        _append_message("early", ms=MS),
    ])
    assert transcript.last_timestamp(path) == "2026-09-09T16:25:59Z"


def test_a_claude_code_iso_stamp_still_wins_its_own_branch(tmp_path):
    path = tmp_path / "cc.jsonl"
    path.write_text(json.dumps(
        {"type": "user", "uuid": "u1", "timestamp": "2026-07-01T10:05:30.500Z",
         "message": {"role": "user", "content": "hi"}}) + "\n")
    assert transcript.last_timestamp(path) == "2026-07-01T10:05:30Z"


def test_a_boolean_time_is_not_a_1970_stamp(tmp_path):
    """`bool` is an `int` in Python. `True / 1000` is 0.001 seconds past the
    epoch: it never WINS the max against a real stamp, but in a log whose only
    numeric `time` is a stray flag it is the only candidate, and the capture
    would report a session that ended in 1970 instead of falling back to the
    file's mtime."""
    assert transcript._kimi_epoch(True) is None
    assert transcript._kimi_epoch(False) is None
    assert transcript._kimi_epoch(MS) == MS / 1000.0
    path = tmp_path / "agents" / "main" / "wire.jsonl"
    path.parent.mkdir(parents=True)
    rows = [_metadata(), {**_append_message("hi"), "time": True}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert transcript.last_timestamp(path) is None


# ---- shapes a truncated or hand-edited log can present ----

def test_an_empty_wire_log_at_the_wire_path_folds_to_nothing(tmp_path):
    """No header to key on and no rows: the path still selects the branch,
    and the header check itself answers False rather than raising on an
    empty object list."""
    assert transcript._is_kimi_wire([]) is False
    path = _write(tmp_path, [])
    assert transcript.from_file(path) == []


def test_malformed_events_contribute_nothing_and_never_raise(tmp_path):
    """One of each shape the fold has to step over: a message that is not an
    object, a role that is neither user nor assistant, content that is not a
    part list, an empty part list, a loop event that is not an object, a tool
    result whose `result` is not an object, and one with no call id. The one
    well-formed prompt beside them is the whole output."""
    system = _append_message("host notice")
    system["message"]["role"] = "system"
    string_content = _append_message("ignored")
    string_content["message"]["content"] = "a bare string, not a part list"
    objs = [
        _metadata(),
        {"type": "context.append_message", "message": "not an object", "time": MS},
        system,
        string_content,
        _append_message(""),
        {"type": "context.append_loop_event", "event": "not an object", "time": MS},
        {"type": "context.append_loop_event", "time": MS,
         "event": {"type": "tool.result", "toolCallId": "call-1",
                   "result": "not an object"}},
        {"type": "context.append_loop_event", "time": MS,
         "event": {"type": "tool.result", "result": {"output": "orphan"}}},
        _append_message("the real prompt"),
    ]
    path = _write(tmp_path, objs)
    assert _roles(transcript.from_file(path)) == [("user", "the real prompt")]


# ---- message ids (#1064) ----
#
# `daimon why --source` and `daimon audit quotes` bind a verbatim item to the
# exact message it quoted through a host-stable per-message `id` (#358).
# Every other adapted host either carries a native one (Claude Code's `uuid`)
# or has none at all (Codex, Windsurf), and `_kimi_tool_message` already
# stamps `id` = `toolCallId` for `tool.result` rows. The remaining Kimi rows —
# the canonical `context.append_message`, its surviving lifecycle mirror, and
# a buffered run of `content.part` fragments — have no native per-message id
# at all, so `why --source` on a Kimi item always fell back to the
# stored-quote disclosure, never the real transcript window.
#
# The id is `kimi-L<n>`, where `n` is the 1-based PHYSICAL line of `wire.jsonl`
# the message started on (the line of the row itself for a single-line
# message; the line of the FIRST contributing `content.part` for a buffered
# assistant run). Not a content hash: two byte-identical messages on
# different lines must never collide (see
# test_identical_prompts_on_different_lines_get_different_ids below). Stable
# under append: `wire.jsonl` only ever grows, so a line's number never
# changes, and an id stamped into a checkpoint's receipt at capture time
# still resolves against the SAME line once the log has grown further.
# Checkpoints captured before this landed carry no Kimi message ids at all
# and keep falling back to the stored-quote disclosure; this module resolves
# ids at READ time and never rewrites a receipt already on disk.

def test_a_context_append_message_gets_a_line_keyed_id(tmp_path):
    # line 1 = metadata, line 2 = the append_message row itself.
    path = _write(tmp_path, [_metadata(), _append_message("hello there")])
    assert transcript.from_file(path)[0]["id"] == "kimi-L2"


def test_a_mirror_survivor_gets_a_line_keyed_id(tmp_path):
    # A prompt whose only surviving record is the lifecycle mirror (the
    # canonical append never landed) still gets an id from ITS OWN line.
    text = "the prompt whose append never landed"
    path = _write(tmp_path, [_metadata(), _turn_prompt(text)])
    msgs = transcript.from_file(path)
    assert msgs[0]["id"] == "kimi-L2"


def test_a_buffered_assistant_run_keys_its_id_on_the_first_fragment_line(
        tmp_path):
    # line 1 = metadata, line 2 = the user prompt, line 3 = the FIRST
    # content.part fragment, line 4 = the second fragment folded into the
    # same assistant message.
    path = _write(tmp_path, [
        _metadata(),
        _append_message("question"),
        _part("text", "First half."),
        _part("text", "Second half."),
    ])
    msgs = transcript.from_file(path)
    assert [m["id"] for m in msgs] == ["kimi-L2", "kimi-L3"]


def test_identical_prompts_on_different_lines_get_different_ids(tmp_path):
    # Do NOT hash content: two byte-identical prompts must not collide.
    path = _write(tmp_path, [
        _metadata(),
        _turn_prompt("one last one", "p-1"),
        _append_message("one last one"),
        _turn_prompt("one last one", "p-2"),
        _append_message("one last one"),
    ])
    ids = [m["id"] for m in transcript.from_file(path)]
    assert len(ids) == len(set(ids)) == 2


def test_ids_never_collide_across_message_and_tool_rows(tmp_path):
    path = _write(tmp_path, [
        _metadata(),
        _append_message("q1"),
        _part("text", "a1"),
        _tool_call("Bash", {"command": "ls"}, "call-1"),
        _tool_result("a\nb", "call-1"),
        _append_message("q2"),
    ])
    ids = [m.get("id") for m in transcript.from_file(path)]
    assert all(ids)
    assert len(ids) == len(set(ids))


def test_ids_are_stable_across_two_reads_of_the_same_file(tmp_path):
    path = _write(tmp_path, [
        _metadata(), _append_message("q"), _part("text", "a")])
    first = [m["id"] for m in transcript.from_file(path)]
    second = [m["id"] for m in transcript.from_file(path)]
    assert first == second


def test_ids_survive_appending_new_lines(tmp_path):
    path = _write(tmp_path, [_metadata(), _append_message("first")])
    before = transcript.from_file(path)[0]["id"]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_append_message("second")) + "\n")
    after = [m["id"] for m in transcript.from_file(path)]
    assert after[0] == before
    assert after[1] != before


def test_serialize_strict_binds_kimi_message_ids_through_the_shipping_pipeline(
    tmp_path, fake_chat_factory
):
    # End to end, the SHIPPING writer (serializer.serialize_strict ->
    # verify_quotes -> provenance.quote_receipt), never a hand-built receipt.
    # Proves the marker -> host-id translation (sanitize_source_ids,
    # message_id_map) needed NO Kimi-specific code: it already reads any
    # message's generic "id" field, so stamping one in _from_kimi_wire is
    # the whole fix.
    # Padded past DAIMON_MIN_MESSAGES (10 non-tool rows by default) with
    # unrelated small talk, same as a real multi-turn session would be.
    path = _write(tmp_path, [
        _metadata(),
        _append_message("please confirm the D-007 prompt"),
        _part("text", "confirmed, adopting the D-007 prompt"),
        _append_message("thanks, one more thing"),
        _part("text", "sure, go ahead"),
        _append_message("what time is it"),
        _part("text", "I don't have a clock"),
        _append_message("no worries"),
        _part("text", "anything else"),
        _append_message("that's all for now"),
        _part("text", "sounds good"),
    ])
    messages = transcript.from_file(path)
    assert messages[0]["id"] == "kimi-L2"
    assert messages[1]["id"] == "kimi-L3"
    script = json.dumps({
        "session_id": "S-kimi",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{
                "text": "adopted the D-007 prompt", "trust": "verbatim",
                "quote": "confirmed, adopting the D-007 prompt",
                "source_message_ids": ["m2"],
            }],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
    })
    chat = fake_chat_factory(script)
    source = {"version": provenance.SOURCE_REF_VERSION, "host": "kimi",
              "session_id": "session_abc", "locator": "managed",
              "author": "alice"}

    cp = serializer.serialize_strict(
        "S-kimi", messages, chat=chat, source_ref=source,
        transcript_hash="a" * 64)

    item = cp["working_context"]["recent_decisions"][0]
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    receipt = item["quote_provenance"]
    assert provenance.valid_quote_receipt(receipt)
    assert receipt["binding"]["mode"] == "message-ids"
    assert receipt["binding"]["message_ids"] == ["kimi-L3"]
