"""`daimon amend propose` binds an exact id through `view.match(how="id")`
(#1132 PR 11b, D11.5): a forgotten id is no target, a quarantined or closed
one is refused on every channel."""

import pytest

from daimon_briefing import amendments, cli, normalize, schema, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/amend-withheld"
Q1 = "SENTINEL-q1 release pipeline awaiting manual approval step"
Q2 = "serializer chunk retry budget unclear"
DECISION = "SENTINEL-d1 adopt the strangler pattern"


@pytest.fixture(autouse=True)
def _human(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


def _write():
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "open_questions": [{"text": Q1, "trust": "inferred"},
                                 {"text": Q2, "trust": "inferred"}],
              "recent_decisions": [{"text": DECISION, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)


def _id(text):
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    for _fld, item in schema.iter_items(cp):
        if item.get("text") == text:
            return item["id"]
    raise AssertionError(text)


def _propose(capsys, item_id, *extra):
    rc = cli.main(["amend", item_id, "--change", "progressed",
                   "--evidence", "the PR merged", *extra])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _amendments():
    return amendments.records(project_dir=PROJECT)


def test_a_visible_open_loop_takes_an_amendment(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q2)
    rc, out, _err = _propose(capsys, item_id, "--by", "agent")
    assert rc == 0 and f"recorded on {item_id}" in out
    assert len(_amendments()) == 1


@pytest.mark.parametrize("by", [[], ["--by", "agent"]], ids=["human", "agent"])
def test_a_quarantined_loop_is_refused_on_every_channel(
        tmp_checkpoint_dir, capsys, by):
    _write()
    trust.propose(text=Q1, kind="question", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    item_id = _id(Q1)
    rc, out, err = _propose(capsys, item_id, *by)
    assert rc == 2
    assert f"error: {item_id} is withheld; a human decides" in err
    assert "SENTINEL-q1" not in out + err
    assert _amendments() == {} or len(_amendments()) == 0


@pytest.mark.parametrize("by", [[], ["--by", "agent"]], ids=["human", "agent"])
def test_a_closed_loop_is_refused_on_every_channel(
        tmp_checkpoint_dir, capsys, by):
    _write()
    item_id = _id(Q2)
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc, out, err = _propose(capsys, item_id, *by)
    assert rc == 2 and f"{item_id} is withheld" in err
    assert Q2 not in out + err


def test_a_forgotten_loop_is_no_target(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q1)
    key = normalize.content_key(Q1)
    store.append_event(item_id, f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT, writer=Writer.HUMAN)
    rc, out, err = _propose(capsys, item_id, "--by", "agent")
    assert rc == 1 and "no open-loop item" in out
    assert "forgotten" not in out + err
    assert len(_amendments()) == 0


def test_a_withheld_decision_is_no_loop_either(tmp_checkpoint_dir, capsys):
    """Only loop-shaped fields take an amendment: a quarantined decision reads
    as no open-loop item, not as a withheld one."""
    _write()
    trust.propose(text=DECISION, kind="decision", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    rc, out, err = _propose(capsys, _id(DECISION))
    assert rc == 1 and "no open-loop item" in out and "withheld" not in out + err
