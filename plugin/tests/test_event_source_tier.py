"""#1293: the authority tier of an events row is OBSERVED from the write path.

`store.append_event` used to default `source` to "cli", a member of
the human sources, so any caller that omitted it wrote a row that reads as
a person's act. The tier is now a required keyword, and the writers that never
passed one (`handoff`, `log`, forget's tombstones and lift) derive it from
the same tty observation the other verbs use. Rows already on disk fold as
before: the human sources are unchanged.
"""
import ast
import inspect
from pathlib import Path

import pytest

from daimon_briefing import authority, cli, forget, jsonl, store
from daimon_briefing.cli import _observe
from daimon_briefing.surfaces import Writer

PROJECT = "/p/event-source-tier"
PACKAGE = Path(store.__file__).parent


def _tty(monkeypatch, value):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: value, raising=False)


def _rows(kind=None):
    rows = jsonl.read(store._events_path(store._resolved(PROJECT))).rows
    return [r for r in rows if kind is None or r.get("kind") == kind]


# ---- the behaviour: handoff and log observe the channel --------------------

@pytest.mark.parametrize("argv", [
    ["handoff", "baton"],
    ["handoff", "--clear"],
    ["log", "--text", "a fact"],
], ids=["handoff", "handoff-clear", "log"])
def test_a_non_tty_write_never_carries_a_human_tier(
        argv, tmp_checkpoint_dir, monkeypatch):
    _tty(monkeypatch, False)
    assert cli.main([*argv, "--project", PROJECT]) == 0
    [row] = _rows()
    assert authority.of_row("events", row) != "human"
    assert row["source"] == "agent"


@pytest.mark.parametrize("argv", [
    ["handoff", "baton"],
    ["handoff", "--clear"],
    ["log", "--text", "a fact"],
], ids=["handoff", "handoff-clear", "log"])
def test_a_terminal_write_carries_the_observed_tty_tier(
        argv, tmp_checkpoint_dir, monkeypatch):
    _tty(monkeypatch, True)
    assert cli.main([*argv, "--project", PROJECT]) == 0
    [row] = _rows()
    assert row["source"] == "cli-tty"
    assert authority.of_row("events", row) == "human"


def test_a_missing_stdin_reads_as_no_terminal(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", None)
    assert _observe.event_source() == "agent"


@pytest.mark.parametrize("exc", [ValueError, OSError])
def test_an_unreadable_stdin_reads_as_no_terminal(monkeypatch, exc):
    def boom():
        raise exc("closed")
    monkeypatch.setattr(cli.sys.stdin, "isatty", boom, raising=False)
    assert _observe.event_source() == "agent"


def test_the_library_is_handed_the_observation_and_never_looks(monkeypatch):
    def boom():
        raise AssertionError("the library must not read stdin")
    monkeypatch.setattr(cli.sys.stdin, "isatty", boom, raising=False)
    assert store.observed_event_source(True) == "cli-tty"
    assert store.observed_event_source(False) == "agent"


# ---- the type: no default, so no omission can produce the human tier -------

def test_append_event_requires_a_source():
    param = inspect.signature(store.append_event).parameters["source"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        store.append_event("o-1", "resolved", project_dir=PROJECT,
                           writer=Writer.HUMAN)


def test_a_spec_without_a_source_is_refused_loudly(tmp_checkpoint_dir):
    with pytest.raises(TypeError):
        store.append_events([{"item_ref": "o-1", "status": "resolved"}],
                            project_dir=PROJECT, writer=Writer.HUMAN)


# ---- the siblings: forget's rows were defaulted human too ------------------

def test_forget_tombstones_carry_the_tier_the_cli_observed(
        tmp_checkpoint_dir, monkeypatch):
    _tty(monkeypatch, True)
    target = forget.Target("k" * 64, "o-aaaaaaaaaaaa", ("o-aaaaaaaaaaaa",),
                           PROJECT, source=_observe.event_source())
    assert forget.write_tombstones(target).reached
    rows = _rows("tombstone")
    assert rows and all(r["source"] == "cli-tty" for r in rows)


def test_a_target_nobody_observed_for_is_an_agents(tmp_checkpoint_dir,
                                                   monkeypatch):
    """The library never reads the terminal: a Target built without the
    CLI's observation writes the lowest tier, even with a terminal attached."""
    _tty(monkeypatch, True)
    target = forget.Target("k" * 64, "o-aaaaaaaaaaaa", ("o-aaaaaaaaaaaa",),
                           PROJECT)
    assert forget.write_tombstones(target).reached
    rows = _rows("tombstone")
    assert rows and all(r["source"] == "agent" for r in rows)


def test_lift_has_no_default_source():
    param = inspect.signature(forget.lift).parameters["source"]
    assert param.default is inspect.Parameter.empty


# ---- the census: every call site passes the tier, checked by AST -----------
# (scar 0054: a text search is satisfied by a comment that names the keyword)

def _call_sites():
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else \
                getattr(func, "id", "")
            if name == "append_event":
                yield path, node


def test_every_append_event_call_passes_source_explicitly():
    sites = list(_call_sites())
    assert len(sites) >= 8, "the census found too few call sites to trust"
    missing = [f"{p.relative_to(PACKAGE)}:{n.lineno}" for p, n in sites
               if "source" not in {k.arg for k in n.keywords}]
    assert missing == []


def _spec_dicts():
    """Dict literals that carry an `item_ref` and a `status` key: the specs
    handed to `append_events`."""
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys
                        if isinstance(k, ast.Constant)}
                if {"item_ref", "status"} <= keys:
                    yield path, node, keys


def test_every_append_events_spec_literal_names_its_source():
    specs = list(_spec_dicts())
    assert specs, "the census found no spec literals"
    missing = [f"{p.relative_to(PACKAGE)}:{n.lineno}" for p, n, keys in specs
               if "source" not in keys]
    assert missing == []
