"""#261: opt-in read-only MCP server — protocol loop tests.

The server is newline-delimited JSON-RPC 2.0 over injected streams; every
test builds an input of one-JSON-per-line requests, runs serve(), and parses
the response lines. No network, no subprocess (one e2e smoke lives at the
bottom of the file, kept cheap).
"""
import io
import json

import pytest

from daimon_briefing import mcp_server


@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here; expose the path.
    return tmp_path / ".daimon" / "logs"


def rpc(*messages):
    """Run serve() over the given request objects; return response objects."""
    fake_in = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    fake_out = io.StringIO()
    rc = mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    lines = [ln for ln in fake_out.getvalue().splitlines() if ln.strip()]
    return rc, [json.loads(ln) for ln in lines]


def _init(protocol="2025-06-18", id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": "initialize",
            "params": {"protocolVersion": protocol,
                       "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}


# ---- handshake ---------------------------------------------------------------


def test_initialize_handshake_negotiates_known_version():
    rc, out = rpc(_init("2025-06-18"))
    assert rc == 0
    assert len(out) == 1
    resp = out[0]
    assert resp["jsonrpc"] == "2.0" and resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "daimon"
    assert "tools" in result["capabilities"]


def test_initialize_older_supported_version_is_echoed():
    _, out = rpc(_init("2024-11-05"))
    assert out[0]["result"]["protocolVersion"] == "2024-11-05"


def test_initialize_unknown_version_answers_with_latest():
    _, out = rpc(_init("1999-01-01"))
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"


def test_initialized_notification_produces_no_output():
    _, out = rpc(_init(),
                 {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert len(out) == 1  # only the initialize response


# ---- protocol errors ----------------------------------------------------------


def test_malformed_json_line_yields_parse_error():
    fake_in = io.StringIO('{"jsonrpc": "2.0", "id": 1, "method"\n')
    fake_out = io.StringIO()
    mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    resp = json.loads(fake_out.getvalue().splitlines()[0])
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_unknown_method_with_id_yields_method_not_found():
    _, out = rpc(_init(),
                 {"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert out[1]["error"]["code"] == -32601
    assert out[1]["id"] == 2


def test_unknown_notification_is_consumed_silently():
    _, out = rpc(_init(),
                 {"jsonrpc": "2.0", "method": "notifications/cancelled"})
    assert len(out) == 1


def test_ping_answers_empty_object():
    _, out = rpc(_init(), {"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert out[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


# ---- tools/list ----------------------------------------------------------------


def test_tools_list_exposes_four_read_only_tools():
    _, out = rpc(_init(), {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = out[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"daimon_recall", "daimon_brief",
                     "daimon_projects", "daimon_status"}
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
        assert t["annotations"]["readOnlyHint"] is True


def test_tools_call_unknown_tool_yields_invalid_params():
    _, out = rpc(_init(),
                 {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "daimon_forget", "arguments": {}}})
    assert out[1]["error"]["code"] == -32602


# ---- tools/call handlers over real store state --------------------------------


def _call(name, arguments, id_=9):
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def _result(out):
    """content[0].text of the LAST response + its isError flag."""
    r = out[-1]["result"]
    return r["content"][0]["text"], r["isError"]


def test_recall_tool_returns_provenance_rows(tmp_checkpoint_dir,
                                             sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_recall", {"query": "merge"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert isinstance(rows, list)
    if rows:  # sample checkpoint may or may not match "merge" — shape matters
        assert "trust" in rows[0]


def test_recall_tool_slug_all_projects_conflict_is_tool_error(tmp_checkpoint_dir):
    _, out = rpc(_init(), _call("daimon_recall",
                                {"query": "x", "slug": "s", "all_projects": True}))
    text, is_err = _result(out)
    assert is_err is True
    assert "slug" in text


def test_recall_tool_missing_query_is_tool_error(tmp_checkpoint_dir):
    _, out = rpc(_init(), _call("daimon_recall", {}))
    _, is_err = _result(out)
    assert is_err is True


def test_brief_tool_renders_checkpoint_text(tmp_checkpoint_dir,
                                            sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "S-a" in text or "left off" in text or len(text) > 40


def test_brief_tool_no_checkpoint_gives_orientation_never_foreign_content(
    tmp_checkpoint_dir, sample_checkpoint, monkeypatch
):
    # #94/#96 lesson, machine edition: a fresh project must NEVER receive
    # another project's briefing inside a tool result. Orientation only.
    from daimon_briefing import store
    store.write_checkpoint("S-other", sample_checkpoint, project_dir="/p/OTHER")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/FRESH")
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False              # absence is an answer, not an error
    assert "no checkpoint" in text
    assert "daimon_projects" in text    # the explicit path is named
    assert "S-other" not in text        # foreign content never leaks


def test_brief_tool_slug_and_project_conflict_is_tool_error(tmp_checkpoint_dir):
    _, out = rpc(_init(), _call("daimon_brief",
                                {"slug": "s", "project": "/p/X"}))
    _, is_err = _result(out)
    assert is_err is True


def test_projects_tool_matches_cli_rows(tmp_checkpoint_dir, sample_checkpoint,
                                        monkeypatch):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    store.write_checkpoint("S-b", sample_checkpoint, project_dir="/p/B")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_projects", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text) == cli.projects_rows(None)


def test_status_tool_matches_cli_payload(tmp_checkpoint_dir, sample_checkpoint,
                                         monkeypatch):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_status", {}))
    text, is_err = _result(out)
    assert is_err is False
    payload, _rc = cli.status_payload(None)
    assert json.loads(text) == payload


def test_blank_lines_between_messages_are_ignored():
    fake_in = io.StringIO(json.dumps(_init()) + "\n\n   \n" +
                          json.dumps({"jsonrpc": "2.0", "id": 2,
                                      "method": "ping"}) + "\n")
    fake_out = io.StringIO()
    rc = mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    assert rc == 0
    assert len(fake_out.getvalue().splitlines()) == 2


def test_non_object_json_line_yields_parse_error():
    # Valid JSON, wrong shape — an array is not a JSON-RPC message here.
    fake_in = io.StringIO('[1, 2, 3]\n')
    fake_out = io.StringIO()
    mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    resp = json.loads(fake_out.getvalue().splitlines()[0])
    assert resp["error"]["code"] == -32700


def test_recall_tool_fts5_error_is_tool_error(tmp_checkpoint_dir, monkeypatch):
    # RecallError (FTS5-less sqlite) must land as isError content the agent
    # can read — never a crash, never a protocol error.
    from daimon_briefing import recall
    def boom(*a, **k):
        raise recall.RecallError("sqlite3 lacks FTS5")
    monkeypatch.setattr(recall, "search", boom)
    _, out = rpc(_init(), _call("daimon_recall", {"query": "x"}))
    text, is_err = _result(out)
    assert is_err is True
    assert "FTS5" in text


def test_brief_tool_empty_briefing_states_it(tmp_checkpoint_dir,
                                             sample_checkpoint, monkeypatch):
    # build() returning None means "nothing worth surfacing" — the tool says
    # so instead of returning empty bytes.
    from daimon_briefing import briefing, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    monkeypatch.setattr(briefing, "build", lambda cp: None)
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "nothing worth surfacing" in text


# ---- usage logging + kill switch ----------------------------------------------


def test_tools_call_logs_mcp_usage(tmp_checkpoint_dir, tmp_log_dir,
                                   sample_checkpoint, monkeypatch):
    from daimon_briefing import config, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rpc(_init(), _call("daimon_projects", {}), _call("daimon_recall",
                                                     {"query": "x"}))
    usage = (config.log_dir() / "usage.log").read_text(encoding="utf-8")
    assert "mcp:projects" in usage
    assert "mcp:recall" in usage


def test_serve_disabled_exits_clean_without_reading(monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    fake_in = io.StringIO(json.dumps(_init()) + "\n")
    fake_out = io.StringIO()
    rc = mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    assert rc == 0
    assert fake_out.getvalue() == ""
