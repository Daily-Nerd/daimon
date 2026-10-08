"""Every MCP handler returns one typed result, and the server renders its
notes as blocks of their own (#1132 PR 9a): the JSON of daimon_recall stays
pure JSON in block 1."""

import dataclasses
import json

import pytest

from daimon_briefing import mcp_server, mcp_tools, recall, store

HOT = "walrusharbor"
VISIBLE = "a plain decision about the walrusharbor deployment"
PROJECT = "/repo/blocks"


@pytest.fixture
def indexed(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": VISIBLE, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    recall.rebuild()


def _call(name, arguments):
    return mcp_server._handle_tools_call({"name": name,
                                          "arguments": arguments})


def test_tool_result_is_a_frozen_text_and_notes_pair():
    got = mcp_tools.ToolResult("hello", ("stale",))
    assert (got.text, got.notes) == ("hello", ("stale",))
    assert mcp_tools.ToolResult("x").notes == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.text = "other"


def test_every_handler_returns_a_tool_result(indexed):
    arguments = {"daimon_recall": {"query": HOT},
                 "daimon_brief": {"project": PROJECT},
                 "daimon_projects": {}, "daimon_status": {},
                 "requests_inbox": {"project": PROJECT}}
    assert set(arguments) == set(mcp_tools.HANDLERS)
    for name, args in arguments.items():
        got = mcp_tools.HANDLERS[name](args)
        assert isinstance(got, mcp_tools.ToolResult), name
        assert isinstance(got.text, str), name


def test_a_clean_recall_is_exactly_one_block(indexed):
    out = _call("daimon_recall", {"query": HOT})
    assert out["isError"] is False
    assert len(out["content"]) == 1
    assert [r["text"] for r in json.loads(out["content"][0]["text"])] == [
        VISIBLE]


def test_a_degraded_recall_keeps_block_one_pure_json_and_notes_after(
        indexed, monkeypatch):
    def boom():
        raise OSError("disk")

    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    out = _call("daimon_recall", {"query": HOT})
    blocks = out["content"]
    assert len(blocks) == 2
    assert all(b["type"] == "text" for b in blocks)
    assert [r["text"] for r in json.loads(blocks[0]["text"])] == [VISIBLE]
    assert blocks[1]["text"].startswith("⚠ recall:")
    assert "out of date" in blocks[1]["text"]
    assert out["isError"] is False


def test_other_handlers_render_one_block(indexed):
    for name, args in (("daimon_brief", {"project": PROJECT}),
                       ("daimon_projects", {}), ("daimon_status", {})):
        assert len(_call(name, args)["content"]) == 1, name


def test_a_tool_error_is_unchanged(indexed):
    out = _call("daimon_recall", {"query": ""})
    assert out["isError"] is True
    assert out["content"] == [{"type": "text", "text": "query is required"}]
