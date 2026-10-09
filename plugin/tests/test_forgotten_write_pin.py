"""A forgotten id is never a write target (#1132 PR 11b, R3). Any later event
on an id lifts its tombstone (`view.forgotten_ids` keeps only ids whose latest
event is a tombstone), so a binding verb that wrote one would resurrect the
value. `resolve`, `reverify` and `amend propose` bind through `view.match` and
write nothing for a tombstone-only id."""

import pytest

from daimon_briefing import amendments, cli, normalize, schema, store, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forgotten-write-pin"
LOOP = "SENTINEL-q1 release pipeline awaiting manual approval step"


@pytest.fixture(autouse=True)
def _human(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


@pytest.fixture
def forgotten_id(tmp_checkpoint_dir):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"open_questions": [
              {"text": LOOP, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)
    body = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                  admit=store.Admit.ANY)
    item_id = next(i["id"] for _f, i in schema.iter_items(body))
    store.append_event(item_id, f"forgotten:{normalize.content_key(LOOP)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)
    assert item_id in view.judge(store.project_slug(PROJECT)).snap.forgotten_ids
    return item_id


def _events():
    path = store.ledger_file(PROJECT, "events.jsonl")
    return path.read_bytes()


@pytest.mark.parametrize("argv", [
    ["resolve", "{id}"],
    ["resolve", "{id}", "--by", "agent", "--evidence", "x"],
    ["reverify", "{id}", "--evidence", "x"],
    ["amend", "{id}", "--change", "progressed", "--evidence", "the PR merged",
     "--by", "agent"],
], ids=["resolve", "resolve-agent", "reverify", "amend-propose"])
def test_a_binding_verb_writes_nothing_for_a_forgotten_id(
        forgotten_id, capsys, argv):
    before = _events()
    rc = cli.main([a.format(id=forgotten_id) for a in argv])
    capsys.readouterr()
    assert rc != 0
    assert _events() == before
    assert view.judge(store.project_slug(PROJECT)).snap.forgotten_ids \
        == frozenset({forgotten_id})
    assert len(amendments.records(project_dir=PROJECT)) == 0


def test_log_cannot_name_an_item_so_it_cannot_lift_a_tombstone():
    """`daimon log` appends a ref-less timeline row (item_ref ""), so it takes
    no id and cannot lift one. The tombstone-lift rule itself (any later event
    on an id, `daimon log` included if it ever took one) is PR 12's decision
    10; this pins that the verb has no id argument today."""
    from daimon_briefing.cli import handoff
    import argparse
    sub = argparse.ArgumentParser().add_subparsers()
    handoff.register(sub, None)
    log = sub.choices["log"]
    dests = {a.dest for a in log._actions}
    assert dests == {"help", "text", "kind", "status", "project"}
