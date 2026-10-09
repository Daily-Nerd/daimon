"""`daimon reverify` binds an exact id through `view.match(how="id")` only
(#1132 PR 11b, D11.5): a forgotten id is no target, a quarantined one binds
on a person's channel with evidence and no echo, a closed one is refused."""

import json

import pytest

from daimon_briefing import cli, normalize, schema, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/reverify-withheld"
Q1 = "SENTINEL-q1 release pipeline awaiting manual approval step"
Q2 = "serializer chunk retry budget unclear"


@pytest.fixture(autouse=True)
def _human(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


def _write():
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"open_questions": [
              {"text": Q1, "trust": "inferred"},
              {"text": Q2, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)


def _id(text):
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    for _fld, item in schema.iter_items(cp):
        if item.get("text") == text:
            return item["id"]
    raise AssertionError(text)


def _events():
    path = store.ledger_file(PROJECT, "events.jsonl")
    return path.read_bytes() if path is not None and path.exists() else b""


def _run(capsys, *argv):
    rc = cli.main(["reverify", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_a_visible_id_reopens_and_the_event_carries_no_text(
        tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q2)
    rc, out, _err = _run(capsys, item_id, "--evidence", "checked it")
    assert rc == 0
    assert out.strip() == f"reopened {item_id}: {Q2}"
    row = json.loads(_events().splitlines()[-1])
    assert row["status"] == "reopened" and "item_text" not in row
    assert Q2.encode() not in _events()


def test_a_quarantined_id_binds_with_evidence_and_no_echo(
        tmp_checkpoint_dir, capsys):
    _write()
    qid = trust.propose(text=Q1, kind="question", reason="fabricated",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=PROJECT)
    item_id = _id(Q1)
    rc, out, err = _run(capsys, item_id, "--evidence", "I checked it by hand")
    assert rc == 0, err
    assert out.strip() == (
        f"reopened {item_id} [question] [withheld: quarantine {qid}]")
    assert "SENTINEL-q1" not in out + err
    assert b"SENTINEL-q1" not in _events()


def test_a_quarantined_id_needs_evidence_even_with_a_live_anchor(
        tmp_checkpoint_dir, capsys):
    _write()
    trust.propose(text=Q1, kind="question", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    rc, out, _err = _run(capsys, _id(Q1))
    assert rc == 1 and "supply --evidence" in out
    assert _events() == b""


def test_a_closed_id_is_refused_with_the_cure(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q2)
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc, out, err = _run(capsys, item_id, "--evidence", "x")
    assert rc == 2
    assert f"error: {item_id} is withheld (trust ledger unreadable); " \
        "daimon trust repair" in err
    assert Q2 not in out + err and _events() == b""


def test_a_forgotten_id_is_not_a_write_target(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q1)
    key = normalize.content_key(Q1)
    store.append_event(item_id, f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT, writer=Writer.HUMAN)
    before = _events()
    rc, out, err = _run(capsys, item_id, "--evidence", "x")
    assert rc == 1 and out.strip() == f"no item found with id {item_id!r}"
    assert "forgotten" not in out + err
    assert _events() == before
