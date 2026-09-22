"""#261: opt-in read-only MCP server — protocol loop tests.

The server is newline-delimited JSON-RPC 2.0 over injected streams; every
test builds an input of one-JSON-per-line requests, runs serve(), and parses
the response lines. No network, no subprocess (one e2e smoke lives at the
bottom of the file, kept cheap).
"""
import io
import json
import time

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


def test_tools_list_exposes_five_read_only_tools():
    _, out = rpc(_init(), {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = out[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"daimon_recall", "daimon_brief", "daimon_projects",
                     "daimon_status", "requests_inbox"}
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


# ---- #1053: the tool call itself was invisible to recall telemetry -----------


def test_recall_tool_writes_a_recall_search_row_with_via_mcp(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_recall", {"query": "merge"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert rows  # "merge" matches the PR #6 open question in sample_checkpoint
    log_path = tmp_log_dir / "recall-delivery.jsonl"
    delivered = [json.loads(ln) for ln in
                 log_path.read_text(encoding="utf-8").splitlines()]
    assert delivered
    assert all(row["surface"] == "recall-search" for row in delivered)
    assert all(row["via"] == "mcp" for row in delivered)
    # #1073: search is unweighted, so the ledger's rank_score is just the
    # same value as match_score, carried through from the returned row.
    assert all(row["rank_score"] == row["match_score"] for row in delivered)


def test_recall_tool_writes_the_empty_pull_row_when_nothing_matches(
        tmp_checkpoint_dir, tmp_log_dir, monkeypatch):
    # #1057: a pull that matched nothing still registers — otherwise an
    # agent that followed the hint and got nothing back reads as an agent
    # that never asked. No checkpoint is written for this project, so the
    # index has nothing to match regardless of query.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/empty")
    _, out = rpc(_init(), _call("daimon_recall", {"query": "zzznomatch"}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text) == []
    log_path = tmp_log_dir / "recall-delivery.jsonl"
    delivered = [json.loads(ln) for ln in
                 log_path.read_text(encoding="utf-8").splitlines()]
    assert len(delivered) == 1
    row = delivered[0]
    assert row["surface"] == "recall-search"
    assert row["via"] == "mcp"
    assert row["item_id"] is None
    assert row["match_score"] is None
    assert row["rendered_chars"] == 0
    assert row["truncated"] is False
    # #1073: this call site never computes a refused candidate — search()
    # has no gate to refuse one behind — so the placeholder's new field
    # defaults to None here, same as an omitted rank_score.
    assert row["best_refused"] is None
    assert row["rank_score"] is None


def test_recall_tool_session_argument_lands_as_injected_into(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call(
        "daimon_recall", {"query": "merge", "session": "S-live-42"}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text)
    log_path = tmp_log_dir / "recall-delivery.jsonl"
    delivered = [json.loads(ln) for ln in
                 log_path.read_text(encoding="utf-8").splitlines()]
    assert delivered
    assert all(row["injected_into"] == "S-live-42" for row in delivered)


def test_recall_tool_accepts_a_kimi_shaped_session_id(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, monkeypatch):
    # #1053 fix A: Kimi's real session id shape (tests/test_kimi_hook_scripts.py
    # SESSION) is `session_` plus a UUID — underscore AND hyphens. The first
    # charset shipped with no underscore, so every Kimi session's tool pull
    # would have silently dropped its session and read as permanent zero
    # follow-through for that host.
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    kimi_session = "session_8a1593d9-376b-43d3-abc9-5796eb848fa3"
    _, out = rpc(_init(), _call(
        "daimon_recall", {"query": "merge", "session": kimi_session}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text)
    log_path = tmp_log_dir / "recall-delivery.jsonl"
    delivered = [json.loads(ln) for ln in
                 log_path.read_text(encoding="utf-8").splitlines()]
    assert delivered
    assert all(row["injected_into"] == kimi_session for row in delivered)


def test_recall_tool_drops_an_untrusted_session_argument_silently(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, monkeypatch):
    # An agent-supplied session must never fail the call — a too-long value,
    # an out-of-charset value, and a non-string all drop the field, not the
    # row, and the tool still answers normally.
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    for bad_session in ("x" * 129, "S with spaces", "S;rm -rf /", 12345, [],
                        None, "S-with-a-\nnewline", 'S-with-a-"quote'):
        log_path = tmp_log_dir / "recall-delivery.jsonl"
        if log_path.exists():
            log_path.unlink()
        _, out = rpc(_init(), _call(
            "daimon_recall", {"query": "merge", "session": bad_session}))
        text, is_err = _result(out)
        assert is_err is False
        assert json.loads(text)
        delivered = [json.loads(ln) for ln in
                     log_path.read_text(encoding="utf-8").splitlines()]
        assert delivered
        assert all(row["injected_into"] is None for row in delivered)


def test_recall_tool_survives_a_telemetry_recorder_that_raises(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Best-effort contract (#1053): a measurement failure must never take
    # down the tool call — the agent still gets its rows back.
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")

    def _boom(*a, **kw):
        raise RuntimeError("telemetry sink is on fire")

    from daimon_briefing import mcp_tools, recall_telemetry
    monkeypatch.setattr(recall_telemetry, "record", _boom)
    monkeypatch.setattr(mcp_tools, "recall_telemetry", recall_telemetry)
    _, out = rpc(_init(), _call("daimon_recall", {"query": "merge"}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text)


def test_recall_tool_input_schema_documents_the_session_argument():
    _, out = rpc(_init(), {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = {t["name"]: t for t in out[1]["result"]["tools"]}
    assert "session" in tools["daimon_recall"]["inputSchema"]["properties"]


# ---- #1079: a readable status field on resolved/superseded/contradicted rows -
#
# `json.dumps(rows)` carried the raw superseded_by/invalidated_by/cured_by
# columns and nothing that turned them into words — an agent reading the tool
# result had to already know those keys existed and what their values meant.
# In practice a demoted row read exactly like a live one. `status` fixes that
# with the SAME wording `daimon recall` (text mode) prints, via the one
# shared helper (recall.describe_status) both surfaces now call.


def test_recall_tool_marks_a_resolved_row(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    from tests.test_recall import _cp

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _cp("S-res", questions=[
        {"text": "meerkat burrow mapping plan colony", "trust": "inferred",
         "id": "o-mee111"}])
    store.write_checkpoint("S-res", cp, project_dir="/p/A")
    store.append_event("o-mee111", "resolved", project_dir="/p/A")

    _, out = rpc(_init(), _call("daimon_recall", {"query": "meerkat"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert rows and rows[0]["status"] == "resolved"


def test_recall_tool_marks_a_superseded_row(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    from tests.test_recall import _cp

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    store.write_checkpoint(
        "S-linked",
        _cp("S-linked", decisions=[
            {"text": "meerkat burrow mapping plan colony",
             "trust": "inferred"}],
            created="2025-01-01T00:00:00Z"),
        project_dir="/p/A")
    newer = _cp(
        "S-new", decisions=[{
            "text": "abandoned meerkat burrow mapping plan colony too unstable",
            "trust": "inferred",
            "links": [{"type": "supersedes",
                       "target": "meerkat burrow mapping plan colony"}]}],
        created="2025-06-01T00:00:00Z")
    store.write_checkpoint("S-new", newer, project_dir="/p/A")

    _, out = rpc(_init(), _call("daimon_recall", {"query": "meerkat"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    old_row = next(r for r in rows
                  if r["text"] == "meerkat burrow mapping plan colony")
    assert old_row["status"] == \
        "superseded by S-new, from a model-authored link"


def test_recall_tool_marks_an_invalidated_row(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    from tests.test_recall import _cp
    from tests.test_recall_invalidated_by import _receipt_row, _write_ledger

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    store.write_checkpoint(
        "S-bad",
        _cp("S-bad", questions=[
            {"text": "the axolotl exporter claim was verified",
             "trust": "inferred", "id": "o-bad111"}],
            created="2026-08-01T00:00:00Z"),
        project_dir="/p/A")
    _write_ledger(store.project_slug("/p/A"), [_receipt_row("o-bad111")])

    _, out = rpc(_init(), _call("daimon_recall",
                                {"query": "axolotl exporter"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert rows and rows[0]["status"] == \
        "contradicted by receipt:receipt-invalid at 2026-08-29T10:00:00Z"


def test_recall_tool_marks_a_cured_row(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store
    from tests.test_recall import _cp
    from tests.test_recall_invalidated_by import (
        _cure_row, _receipt_row, _write_ledger)

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    store.write_checkpoint(
        "S-1",
        _cp("S-1", questions=[
            {"text": "the axolotl exporter claim was verified",
             "trust": "inferred", "id": "o-111aaa"}],
            created="2026-08-01T00:00:00Z"),
        project_dir="/p/A")
    _write_ledger(store.project_slug("/p/A"), [
        _receipt_row("o-111aaa"), _cure_row("o-111aaa")])

    _, out = rpc(_init(), _call("daimon_recall",
                                {"query": "axolotl exporter"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert rows and rows[0]["status"] == (
        "contradiction cleared by receipt-ok:receipt-valid "
        "at 2026-08-29T12:00:00Z")


def test_recall_tool_live_row_carries_a_null_status(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_recall", {"query": "merge"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert rows
    assert all(r["status"] is None for r in rows)


def test_recall_tool_status_matches_the_cli_text_mode_wording(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """One shared helper, not two renderers that could drift: the MCP
    `status` field and `daimon recall`'s own bracketed marker must describe
    the SAME resolved row identically (#1079)."""
    from daimon_briefing import cli, store
    from tests.test_recall import _cp

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _cp("S-res", questions=[
        {"text": "meerkat burrow mapping plan colony", "trust": "inferred",
         "id": "o-mee111"}])
    store.write_checkpoint("S-res", cp, project_dir="/p/A")
    store.append_event("o-mee111", "resolved", project_dir="/p/A")

    _, out = rpc(_init(), _call("daimon_recall", {"query": "meerkat"}))
    text, _ = _result(out)
    status = json.loads(text)[0]["status"]

    rc = cli.main(["recall", "meerkat", "--project", "/p/A"])
    assert rc == 0
    cli_line = [ln for ln in capsys.readouterr().out.splitlines()
               if "meerkat" in ln][0]
    assert f"[{status}]" in cli_line


def test_recall_tool_status_field_is_not_persisted_to_telemetry(
        tmp_checkpoint_dir, tmp_log_dir, monkeypatch):
    """The status field is added AFTER recall_telemetry.record() runs, on the
    rows being serialized — the telemetry row shape must stay exactly what it
    was before #1079."""
    from daimon_briefing import store
    from tests.test_recall import _cp

    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _cp("S-res", questions=[
        {"text": "meerkat burrow mapping plan colony", "trust": "inferred",
         "id": "o-mee111"}])
    store.write_checkpoint("S-res", cp, project_dir="/p/A")
    store.append_event("o-mee111", "resolved", project_dir="/p/A")

    _, out = rpc(_init(), _call("daimon_recall", {"query": "meerkat"}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text)[0]["status"] == "resolved"

    log_path = tmp_log_dir / "recall-delivery.jsonl"
    delivered = [json.loads(ln) for ln in
                log_path.read_text(encoding="utf-8").splitlines()]
    assert delivered
    assert "status" not in delivered[0]


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


def test_brief_tool_never_carries_the_request_panel(tmp_checkpoint_dir,
                                                     sample_checkpoint,
                                                     monkeypatch):
    # #694 PR 2 (D2): daimon_brief is untouched — the panel injects ONLY on
    # the CLI same-project brief path. Without a gate, every MCP client
    # would auto-receive foreign ask/why/from_label prose (#94/#96).
    from daimon_briefing import requests, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    store.write_checkpoint("S-mcp-sender", {
        "session_id": "S-mcp-sender", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir="/p/mcp-brief-sender")
    requests.open_request(to=store.project_slug("/p/A"),
                          ask="publish the schema", why="because",
                          channel="cli-agent",
                          project_dir="/p/mcp-brief-sender")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "publish the schema" not in text
    assert "Requests waiting on you" not in text
    # And the tool never stamped a surfaced row either — no side effect from
    # a read-only MCP call.
    assert requests.needs_surfaced_stamp(
        next(iter(requests.recipient_join(project_dir="/p/A").values())))


def test_brief_tool_never_carries_the_decision_count_line(tmp_checkpoint_dir,
                                                           sample_checkpoint,
                                                           monkeypatch):
    # #766 slice 5: no MCP exposure — the count line joins the same excluded
    # family as the request/verdict/owed panels above. A decision IS
    # actually waiting here, so this is a meaningful negative, not a vacuous
    # one: the line would render on the CLI same-project path.
    from daimon_briefing import pending, requests, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    store.write_checkpoint("S-mcp-sender-2", {
        "session_id": "S-mcp-sender-2", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir="/p/mcp-brief-sender-2")
    requests.open_request(to=store.project_slug("/p/A"),
                          ask="publish the schema", why="because",
                          channel="cli-agent",
                          project_dir="/p/mcp-brief-sender-2")
    # Proof the negative below is not vacuous: /p/A's OWN decide queue
    # genuinely has something waiting, mirroring the request-panel test
    # neighbor's needs_surfaced_stamp proof above.
    assert pending.queue(project_dir="/p/A")["rows"]
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "decisions waiting on you" not in text
    assert "decision waiting on you" not in text


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
    # #390: the payload embeds a checkpoint age truncated to whole seconds,
    # recomputed from the live clock on each call. Freeze it so the tool call
    # and status_payload see the same instant — a diff must mean the payloads
    # diverged, not that a second ticked over between them.
    frozen = time.time()
    monkeypatch.setattr(time, "time", lambda: frozen)
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


# ---- requests_inbox (#694 PR 2) -----------------------------------------------


def test_requests_inbox_tool_returns_addressed_rows(tmp_checkpoint_dir,
                                                     monkeypatch):
    from daimon_briefing import requests, store
    store.write_checkpoint("S-sender", {
        "session_id": "S-sender", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "the sender shipped something", "trust": "inferred"}]},
    }, project_dir="/p/mcp-sender")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/mcp-recipient")
    to = store.project_slug("/p/mcp-recipient")
    q_id = requests.open_request(to=to, ask="publish the schema",
                                 why="the client needs it", channel="cli-agent",
                                 project_dir="/p/mcp-sender")
    _, out = rpc(_init(), _call("requests_inbox", {}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert [r["request_id"] for r in rows] == [q_id]
    assert rows[0]["ask"] == "publish the schema"


def test_requests_inbox_tool_takes_an_explicit_project(tmp_checkpoint_dir,
                                                        monkeypatch):
    from daimon_briefing import requests, store
    store.write_checkpoint("S-sender", {
        "session_id": "S-sender", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir="/p/mcp-sender-b")
    to = store.project_slug("/p/mcp-recipient-b")
    q_id = requests.open_request(to=to, ask="review this", why="because",
                                 channel="cli-agent",
                                 project_dir="/p/mcp-sender-b")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/somewhere-else")
    _, out = rpc(_init(),
                _call("requests_inbox", {"project": "/p/mcp-recipient-b"}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert [r["request_id"] for r in rows] == [q_id]


def test_requests_inbox_tool_empty_is_an_empty_list(tmp_checkpoint_dir,
                                                     monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/mcp-nobody-asked")
    _, out = rpc(_init(), _call("requests_inbox", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert json.loads(text) == []


def test_mcp_tools_expose_no_request_write_verb():
    # #694 PR 2: the tool tier is read-only by design — open/revise/accept/
    # reject/needs-info/suppress/done stay CLI-only.
    from daimon_briefing import mcp_tools
    assert "requests_inbox" in mcp_tools.HANDLERS
    for verb in ("open", "revise", "accept", "reject", "needs_info",
                "suppress", "done"):
        assert not any(verb in name for name in mcp_tools.HANDLERS)


# ---- usage logging + kill switch ----------------------------------------------


def test_tools_call_logs_mcp_usage(tmp_checkpoint_dir, tmp_log_dir,
                                   sample_checkpoint, monkeypatch):
    from daimon_briefing import config, store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rpc(_init(), _call("daimon_projects", {}), _call("daimon_recall",
                                                     {"query": "x"}),
       _call("requests_inbox", {}))
    usage = (config.log_dir() / "usage.log").read_text(encoding="utf-8")
    assert "mcp:projects" in usage
    assert "mcp:recall" in usage
    assert "mcp:requests_inbox" in usage


def test_serve_disabled_exits_clean_without_reading(monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    fake_in = io.StringIO(json.dumps(_init()) + "\n")
    fake_out = io.StringIO()
    rc = mcp_server.serve(in_stream=fake_in, out_stream=fake_out)
    assert rc == 0
    assert fake_out.getvalue() == ""


# ---- #899: tenant scope refuses caller-chosen addressing over MCP --------


def _tenant_two_buckets(sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    store.write_checkpoint("S-b", sample_checkpoint, project_dir="/p/B")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")


@pytest.mark.parametrize("tool,args", [
    ("daimon_recall", {"query": "x", "slug": "-p-B"}),
    ("daimon_recall", {"query": "x", "all_projects": True}),
    ("daimon_brief", {"slug": "-p-B"}),
])
def test_tenant_scope_refuses_caller_chosen_scope_over_mcp(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch, tool, args):
    _tenant_two_buckets(sample_checkpoint, monkeypatch)
    _, out = rpc(_init(), _call(tool, args))
    text, is_err = _result(out)
    assert is_err is True
    assert "tenant-scoped" in text


def test_tenant_scope_projects_tool_lists_only_the_callers_own(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    from daimon_briefing import store
    _tenant_two_buckets(sample_checkpoint, monkeypatch)
    _, out = rpc(_init(), _call("daimon_projects", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert [r["slug"] for r in json.loads(text)] == [store.project_slug("/p/A")]


def test_tenant_scope_leaves_own_scope_reads_working_over_mcp(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    _tenant_two_buckets(sample_checkpoint, monkeypatch)
    _, out = rpc(_init(), _call("daimon_recall", {"query": "merge"}))
    _, is_err = _result(out)
    assert is_err is False
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "daimon_projects" not in text  # no enumeration hint either
