"""#1132 PR 7b-2: `effects_commit` and the hosts that use it. The contract:
build the output, write it, flush stdout, THEN commit; usage also commits when
the verb exits 1 or 2; stamps and ledger rows only after a shown briefing."""

import io
import json

import pytest

from daimon_briefing import (briefing, cli, effects_commit, mcp_tools,
                             recall_telemetry, requests, store, view, worldcheck)
from daimon_briefing.effects import (Effects, Surfaced, Telemetry,
                                     Verification)


class Recorder(io.StringIO):
    """A stdout that logs its writes and flushes into `events`."""

    def __init__(self, events):
        super().__init__()
        self.events = events

    def write(self, text):
        if text:
            self.events.append("out")
        return super().write(text)

    def flush(self):
        self.events.append("flush")
        super().flush()


class Log(list):
    def tap(self, monkeypatch):
        """Route stdout into this log. Called in the test body: pytest's
        capture puts its own stdout back when the call phase starts, so a
        fixture-time patch would be lost."""
        monkeypatch.setattr("sys.stdout", Recorder(self))


@pytest.fixture
def events(monkeypatch):
    log = Log()
    monkeypatch.setattr(cli, "_note_usage", lambda tag: log.append(("usage", tag)))
    return log


def _after_output(events, first_write):
    """Nothing in `events` that is a write precedes the last output and a
    flush; `first_write` is the first non-output entry."""
    last_out = max(i for i, e in enumerate(events) if e == "out")
    flushes = [i for i, e in enumerate(events) if e == "flush"]
    idx = next(i for i, e in enumerate(events) if first_write(e))
    assert idx > last_out, events
    assert any(last_out < f < idx for f in flushes), events


def _is_usage(entry):
    return isinstance(entry, tuple) and entry[0] == "usage"


# ---- commit itself ----


def test_commit_of_nothing_flushes_nothing_and_writes_nothing(events, monkeypatch):
    events.tap(monkeypatch)
    effects_commit.commit(Effects.none())
    assert events == []


def test_commit_flushes_then_writes_usage_in_order(events, monkeypatch):
    events.tap(monkeypatch)
    effects_commit.commit(Effects(usage=("a", "b")))
    assert events == ["flush", ("usage", "a"), ("usage", "b")]


def test_one_failing_write_blocks_no_other(monkeypatch, events):
    events.tap(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(requests, "stamp_surfaced", boom)
    monkeypatch.setattr(cli, "_write_worldcheck_ledger", boom)
    monkeypatch.setattr(recall_telemetry, "record", boom)
    effects_commit.commit(Effects(
        usage=("u",),
        verification=(Verification("/p", "/p", {"fired": 1}, ()),),
        surfaced=(Surfaced("request", "/p", "q-aaaaaaaaaaaa", None),),
        telemetry=(Telemetry([], {}),)))
    assert ("usage", "u") in events and ("usage", "worldcheck:fired") in events


def test_commit_survives_a_stdout_that_cannot_flush(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_note_usage", seen.append)

    class Broken(io.StringIO):
        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr("sys.stdout", Broken())
    effects_commit.commit(Effects(usage=("u",)))
    assert seen == ["u"]


def test_surfaced_effects_only_for_cards_that_owe_a_stamp():
    card = briefing.Card
    printed = {"request": (card("q-1", True), card("q-2", False)),
               "verdict": (card("q-3", True, "ev1"),)}
    got = effects_commit.surfaced_effects("/p", printed)
    assert got.surfaced == (Surfaced("request", "/p", "q-1", None),
                            Surfaced("verdict", "/p", "q-3", "ev1"))
    assert effects_commit.surfaced_effects(None, printed) == Effects.none()
    assert effects_commit.surfaced_effects("/p", None) == Effects.none()


def test_committing_commits_when_the_verb_raises(events, monkeypatch):
    events.tap(monkeypatch)
    @effects_commit.committing
    def verb(args, fx):
        fx.add(Effects(usage=("v",)))
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        verb(None)
    assert ("usage", "v") in events


def test_the_commit_helper_calls_stamps_through_the_requests_chokepoint():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(effects_commit.__file__).read_text(encoding="utf-8"))
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert {"stamp_surfaced", "stamp_verdict_surfaced"} <= attrs
    assert not {"append", "_write_verdict_row", "_stamp"} & attrs


# ---- CLI hosts: output before any write ----


def _checkpoint(project, session="S-ec"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project)


def test_brief_writes_its_usage_stamps_and_ledger_after_the_output(
        tmp_checkpoint_dir, monkeypatch, events):
    events.tap(monkeypatch)
    recipient = "/p/ec-recipient"
    _checkpoint(recipient)
    _checkpoint("/p/ec-sender", "S-ec-s")
    rid = requests.open_request(
        to=store.project_slug(recipient), ask="publish the schema",
        why="because", channel="cli-agent",
        project_dir=store.project_slug("/p/ec-sender"))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setattr(worldcheck, "check", lambda cp, project: {
        "fired": 1, worldcheck.LEDGER_KEY: []})
    real = requests.stamp_surfaced

    def stamp(*a, **k):
        events.append(("stamp", a[0]))
        return real(*a, **k)

    monkeypatch.setattr(requests, "stamp_surfaced", stamp)
    monkeypatch.setattr(cli, "_write_worldcheck_ledger",
                        lambda rows, route: events.append(("ledger",)))
    assert cli.main(["brief"]) == 0
    _after_output(events, lambda e: e not in ("out", "flush"))
    kinds = [e[0] for e in events if isinstance(e, tuple)]
    assert kinds[0] == "usage" and "stamp" in kinds and "ledger" in kinds
    assert ("usage", "brief") in events and ("stamp", rid) in events


def test_loops_writes_its_usage_after_the_output(tmp_checkpoint_dir,
                                                 monkeypatch, events):
    events.tap(monkeypatch)
    _checkpoint("/p/ec-loops")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-loops")
    assert cli.main(["loops"]) == 0
    _after_output(events, _is_usage)
    assert ("usage", "loops") in events


def test_status_and_projects_write_their_usage_after_the_output(
        tmp_checkpoint_dir, monkeypatch, events):
    events.tap(monkeypatch)
    _checkpoint("/p/ec-sp")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-sp")
    for argv, tag in ((["status"], "status"), (["projects"], "projects"),
                      (["status", "--suppressed"], "status")):
        events.clear()
        cli.main(argv)
        _after_output(events, _is_usage)
        assert ("usage", tag) in events


# ---- usage still records when the verb fails ----


def test_brief_records_usage_when_it_refuses_with_rc_2(
        tmp_checkpoint_dir, monkeypatch, events, capsys):
    events.tap(monkeypatch)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-x")
    assert cli.main(["brief", "--slug", "a", "--project", "b"]) == 2
    assert ("usage", "brief") in events
    events.clear()
    assert cli.main(["brief", "--auto", "--slug", "nothing-here"]) == 1
    assert ("usage", "brief:auto") in events


def test_brief_records_usage_when_the_view_cannot_be_built(
        tmp_checkpoint_dir, monkeypatch, events, capsys):
    events.tap(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("view")

    monkeypatch.setattr(briefing, "prepare", boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-y")
    assert cli.main(["brief"]) == 2
    assert cli.main(["loops"]) == 2
    assert ("usage", "brief") in events and ("usage", "loops") in events


def test_projects_records_usage_when_it_exits_2(tmp_checkpoint_dir,
                                                monkeypatch, events, capsys):
    events.tap(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("peek")

    monkeypatch.setattr(view, "visible_topic", boom)
    _checkpoint("/p/ec-peek")
    assert cli.main(["projects"]) == 2
    assert ("usage", "projects") in events


def test_a_brief_that_failed_stamps_nothing(tmp_checkpoint_dir, monkeypatch,
                                            events, capsys):
    events.tap(monkeypatch)
    recipient = "/p/ec-nostamp"
    _checkpoint(recipient)
    _checkpoint("/p/ec-nostamp-s", "S-ec-ns")
    requests.open_request(
        to=store.project_slug(recipient), ask="ask", why="because",
        channel="cli-agent", project_dir=store.project_slug("/p/ec-nostamp-s"))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)

    def boom(*a, **k):
        raise RuntimeError("render")

    monkeypatch.setattr("daimon_briefing.render.render_brief", boom)
    with pytest.raises(RuntimeError):
        cli.main(["brief"])
    assert ("usage", "brief") in events
    assert not [e for e in requests.events(project_dir=recipient)
                if e.get("event") == "surfaced"]


# ---- MCP: usage after the response is built ----


def test_every_mcp_handler_records_usage_after_its_response_is_built(
        tmp_checkpoint_dir, monkeypatch, events):
    events.tap(monkeypatch)
    _checkpoint("/p/ec-mcp")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-mcp")
    built = []
    real_render = briefing.render_plain
    monkeypatch.setattr(briefing, "render_plain",
                        lambda *a, **k: built.append(1) or real_render(*a, **k))
    mcp_tools.HANDLERS["daimon_brief"]({})
    assert built and events == ["flush", ("usage", "mcp:brief")]
    for name, tag in (("daimon_projects", "mcp:projects"),
                      ("daimon_status", "mcp:status"),
                      ("requests_inbox", "mcp:requests_inbox"),
                      ("daimon_recall", "mcp:recall")):
        arguments = {"query": "x"} if name == "daimon_recall" else {}
        events.clear()
        mcp_tools.HANDLERS[name](arguments)
        assert events == ["flush", ("usage", tag)], (name, events)


def test_the_mcp_brief_usage_follows_the_prepare(tmp_checkpoint_dir,
                                                 monkeypatch, events):
    events.tap(monkeypatch)
    order = []
    real = briefing.prepare
    monkeypatch.setattr(briefing, "prepare",
                        lambda *a, **k: order.append("prepare") or real(*a, **k))
    monkeypatch.setattr(cli, "_note_usage",
                        lambda tag: order.append(("usage", tag)))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/ec-order")
    mcp_tools.HANDLERS["daimon_brief"]({})
    assert order == ["prepare", ("usage", "mcp:brief")]


def test_mcp_usage_records_when_the_handler_raises_a_tool_error(
        tmp_checkpoint_dir, monkeypatch, events):
    events.tap(monkeypatch)
    with pytest.raises(mcp_tools.ToolError):
        mcp_tools.HANDLERS["daimon_brief"]({"slug": "a", "project": "b"})
    with pytest.raises(mcp_tools.ToolError):
        mcp_tools.HANDLERS["daimon_recall"]({"query": ""})
    assert ("usage", "mcp:brief") in events and ("usage", "mcp:recall") in events


def test_mcp_recall_telemetry_commits_after_the_rows_are_built(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    store.write_checkpoint("S-a", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    seen = []
    real = recall_telemetry.record

    def record(rows, **kw):
        seen.append([dict(r) for r in rows])
        return real(rows, **kw)

    monkeypatch.setattr(recall_telemetry, "record", record)
    out = json.loads(mcp_tools.HANDLERS["daimon_recall"]({"query": "merge"}))
    assert out and seen
    assert all("status" not in r for r in seen[0])


# ---- Hermes: empty effects ----


def test_hermes_pre_llm_call_records_no_usage(tmp_checkpoint_dir,
                                              sample_checkpoint, monkeypatch):
    from daimon_briefing import hooks
    store.write_checkpoint("S-prev", sample_checkpoint)
    monkeypatch.setattr(cli, "_note_usage", lambda tag: pytest.fail(tag))
    out = hooks.pre_llm_call(session_id="S2", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert out and "context" in out


# ---- every host goes through the seam ----


def _calls(tree, name):
    import ast
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 or (isinstance(n.func, ast.Name) and n.func.id == name))]


def test_every_host_entry_point_goes_through_effects_commit():
    """One census: the CLI verbs are wrapped by `committing`, the MCP
    handlers by `_tool` (which commits in a finally), and Hermes commits its
    pending effects in a finally."""
    import ast
    from pathlib import Path

    import daimon_briefing as pkg
    root = Path(pkg.__file__).parent

    def tree(rel):
        return ast.parse((root / rel).read_text(encoding="utf-8"))

    def decorated(rel, names, deco):
        t = tree(rel)
        got = {n.name for n in ast.walk(t) if isinstance(n, ast.FunctionDef)
               and any(ast.unparse(d) == deco for d in n.decorator_list)}
        assert set(names) <= got, (rel, set(names) - got)

    decorated("cli/brief.py", ["_cmd_brief"], "effects_commit.committing")
    decorated("cli/lifecycle.py", ["_cmd_loops"], "effects_commit.committing")
    decorated("cli/status.py", ["_cmd_status"], "effects_commit.committing")
    decorated("cli/projects.py", ["_cmd_projects"], "effects_commit.committing")
    mcp = tree("mcp_tools.py")
    for name, tool in (("_recall", "recall"), ("_brief", "brief"),
                       ("_projects", "projects"), ("_status", "status"),
                       ("_requests_inbox", "requests_inbox")):
        fn = next(n for n in mcp.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        assert [ast.unparse(d) for d in fn.decorator_list] == [
            f"_tool('{tool}')"], name
    assert any(_calls(mcp, "commit"))
    for rel, fn_name in (("hooks.py", "pre_llm_call"),):
        fn = next(n for n in ast.walk(tree(rel))
                  if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)
                 and n.finalbody and any(_calls(ast.Module(b, []), "commit")
                                         for b in n.finalbody)]
        assert tries, f"{rel}:{fn_name} does not commit in a finally"
