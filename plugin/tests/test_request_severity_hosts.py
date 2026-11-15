"""#1348: host parity. The CLI is the only request write surface on every
host, so the severity reaches MCP and the host API as the fold's own fields,
and the filter is one keyword on the requests function all three share."""

import json

import pytest

from daimon_briefing import api, pending, requests, store
from daimon_briefing.surfaces import Writer

from .test_mcp_server import _call, _init, _result, rpc

REASON = "the release waits on this"
RDIR = "/p/sev-host-recipient"


@pytest.fixture
def asks(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RDIR)
    store.write_checkpoint("S-sender", {
        "session_id": "S-sender", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "the sender shipped something", "trust": "inferred"}]},
    }, project_dir="/p/sev-host-sender", writer=Writer.HUMAN)
    to = store.project_slug(RDIR)
    ids = {}
    for level in requests.SEVERITIES:
        extra = ({} if level == requests.DEFAULT_SEVERITY
                 else {"severity": level, "severity_reason": REASON})
        ids[level] = requests.open_request(
            to=to, ask=f"ask at {level}", why="because", channel="cli-agent",
            project_dir="/p/sev-host-sender", **extra)
    return ids


def _tool(name):
    _, out = rpc(_init(), {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    return next(t for t in out[1]["result"]["tools"] if t["name"] == name)


def test_the_mcp_schema_takes_min_severity_with_the_tables_enum():
    prop = _tool("requests_inbox")["inputSchema"]["properties"][
        "min_severity"]
    assert prop["enum"] == list(requests.SEVERITIES)


def test_mcp_returns_the_explicit_severity_on_every_row(asks):
    _, out = rpc(_init(), _call("requests_inbox", {}))
    text, is_err = _result(out)
    assert is_err is False
    rows = json.loads(text)
    assert {r["request_id"]: r["severity"] for r in rows} == {
        v: k for k, v in asks.items()}
    assert all("severity_reason" in r for r in rows)


def test_mcp_min_severity_filters_through_the_shared_keyword(asks):
    _, out = rpc(_init(), _call("requests_inbox", {"min_severity": "high"}))
    text, _ = _result(out)
    assert [r["severity"] for r in json.loads(text)] == ["critical", "high"]


def test_mcp_refuses_a_level_outside_the_table(asks):
    _, out = rpc(_init(), _call("requests_inbox", {"min_severity": "urgent"}))
    text, is_err = _result(out)
    assert is_err is True
    for level in requests.SEVERITIES:
        assert level in text


def test_the_host_api_shares_the_same_keyword(asks):
    got = api.inbox(RDIR, min_severity="high")
    assert [r["severity"] for r in got.rows] == ["critical", "high"]
    assert [r["severity"] for r in api.inbox_listing(
        RDIR, min_severity="normal")] == ["critical", "high", "normal"]
    assert len(api.listing("/p/sev-host-sender", min_severity="low")) == 4


def test_the_host_queue_rows_carry_both_keys(asks):
    rows = pending.queue(project_dir=RDIR)["rows"]
    assert rows and all("severity" in r and "severity_reason" in r
                        for r in rows)
    assert [r["severity"] for r in rows] == ["critical", "high", "normal",
                                             "low"]
