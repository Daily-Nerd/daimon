import json

from daimon_briefing import provenance, serializer, tool_context, transcript


def _receipt(source_id="a-source"):
    source = {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": "S1",
        "locator": "managed",
    }
    return provenance.quote_receipt(
        source, {"algorithm": "sha256", "scope": "raw-file", "value": "a" * 64},
        outcome="verified", checked_at="2026-09-10T00:00:00Z",
        binding_mode="message-ids", message_ids=(source_id,),
    )


def _coverage(rows):
    return {"version": 1, "adapter": "claude-code", "available": True,
            "rows": rows}


def _item(source_id="a-source"):
    return {"text": "claim", "trust": "verbatim", "quote": "claim",
            "quote_provenance": _receipt(source_id)}


def test_detailed_loader_keeps_messages_and_records_claude_rows(tmp_path):
    path = tmp_path / "S1.jsonl"
    rows = [
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "prompt"}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": "answer"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    detailed = transcript.from_file_detailed(path)

    assert detailed["messages"] == [
        {"role": "user", "content": "prompt", "id": "u1"},
        {"role": "assistant", "content": "answer", "id": "a1"},
    ]
    assert detailed["coverage"]["available"] is True
    assert detailed["coverage"]["rows"][0]["host_user_input"] is True


def test_observed_context_is_bounded_and_ordered():
    messages = [
        {"role": "user", "id": "u1", "content": "prompt"},
        {"role": "tool", "id": "t1", "content": "error", "tool_result": True},
        {"role": "assistant", "id": "a-source", "content": "claim"},
    ]
    coverage = _coverage([
        {"id": "u1", "role": "user", "tool_result": False, "host_user_input": True},
        {"id": "t1", "role": "user", "tool_result": True, "host_user_input": False},
        {"id": "a-source", "role": "assistant", "tool_result": False, "host_user_input": False},
    ])

    got = tool_context.derive(_item(), messages, coverage)

    assert got["status"] == "observed"
    assert got["contexts"] == [{
        "source_message_id": "a-source",
        "preceding_tool_result_ids": ["t1"],
        "boundary": "host_user_input",
        "messages_examined": 1,
    }]


def test_unsupported_or_incomplete_capture_is_explicitly_unavailable():
    item = _item()
    assert tool_context.derive(item, [], None)["reason"] == "coverage_unknown"
    coverage = {"version": 1, "adapter": "claude-code", "available": False,
                "reason": "coverage_incomplete", "rows": []}
    assert tool_context.derive(item, [], coverage)["reason"] == "coverage_incomplete"


def test_signal_only_source_is_not_an_assistant_anchor():
    item = _item("tool-id")
    messages = [{"role": "tool", "id": "tool-id", "content": "output",
                 "tool_result": True}]
    coverage = _coverage([{"id": "tool-id", "role": "user", "tool_result": True,
                           "host_user_input": False}])
    assert tool_context.derive(item, messages, coverage)["reason"] == "source_not_assistant"


def test_boundary_immediately_before_source_produces_observed_empty_window():
    messages = [
        {"role": "user", "id": "u1", "content": "prompt"},
        {"role": "assistant", "id": "a-source", "content": "claim"},
    ]
    coverage = _coverage([
        {"id": "u1", "role": "user", "tool_result": False, "host_user_input": True},
        {"id": "a-source", "role": "assistant", "tool_result": False, "host_user_input": False},
    ])

    got = tool_context.derive(_item(), messages, coverage)

    assert got["status"] == "observed"
    assert got["contexts"][0]["preceding_tool_result_ids"] == []
    assert got["contexts"][0]["messages_examined"] == 0


def test_short_prefix_without_boundary_is_unavailable():
    messages = [{"role": "assistant", "id": "a-source", "content": "claim"}]
    coverage = _coverage([{"id": "a-source", "role": "assistant",
                           "tool_result": False, "host_user_input": False}])

    assert tool_context.derive(_item(), messages, coverage)["reason"] == "boundary_unknown"


def test_model_supplied_context_is_stripped_before_admission():
    checkpoint = {"working_context": {
        "active_topic": {"text": "topic", "trust": "inferred",
                          "preceding_tool_context": {"status": "observed"}},
        "open_questions": [], "recent_decisions": [],
    }, "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []}}

    serializer.strip_code_owned_keys(checkpoint)

    assert "preceding_tool_context" not in checkpoint["working_context"]["active_topic"]
