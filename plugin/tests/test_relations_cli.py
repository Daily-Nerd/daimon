"""`daimon relations` adjudication verbs (#678 Phase 2→3 bridge).

Verdicts are human-only: there is no `--by agent` escape because agents
cannot confirm relations at all — the channel is observed (tty), never
claimed. The list surface resolves endpoint texts at read time (the ledger
itself holds no text) and withholds edges touching erased endpoints.
"""

import pytest

from daimon_briefing import cli, relations, store

PROJECT = "/repo/relations-cli-arc"


@pytest.fixture
def seeded(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [
                {"text": "keep the relations ledger append only",
                 "trust": "inferred"},
                {"text": "adjudicate candidates through the tty path",
                 "trust": "inferred"}]},
    }, project_dir=PROJECT)
    stored = store.read_latest(project_dir=PROJECT, fallback=False)
    ids = [i["id"] for i in stored["working_context"]["recent_decisions"]]
    rel_id = relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S1", "field": "recent_decisions",
                       "item_id": ids[0]},
        to_endpoint={"session_id": "S1", "field": "recent_decisions",
                     "item_id": ids[1]},
        matched_by=["carry-absolute"],
        matcher_version="lab-2026-08-12",
        channel="lab-import",
        project_dir=PROJECT,
    )
    return {"rel_id": rel_id, "item_ids": ids}


def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def test_list_resolves_texts_at_read_time(seeded, capsys):
    assert cli.main(["relations", "list"]) == 0
    out = capsys.readouterr().out
    assert seeded["rel_id"] in out
    assert "keep the relations ledger append only" in out
    assert "candidate" in out


def test_list_empty_project_says_so(tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/repo/relations-empty")
    assert cli.main(["relations", "list"]) == 0
    assert "no relations" in capsys.readouterr().out


def test_show_prints_proposal_history(seeded, capsys):
    assert cli.main(["relations", "show", seeded["rel_id"]]) == 0
    out = capsys.readouterr().out
    assert "lab-2026-08-12" in out
    assert "carry-absolute" in out


def test_show_unknown_relation_fails(seeded, capsys):
    assert cli.main(["relations", "show", "rel-" + "f" * 16]) == 1


def test_confirm_refuses_without_a_terminal(seeded, capsys):
    assert cli.main(["relations", "confirm", seeded["rel_id"]]) == 1
    assert "terminal" in capsys.readouterr().out
    state = relations.records(project_dir=PROJECT)[seeded["rel_id"]]["state"]
    assert state == "candidate"


def test_confirm_on_a_terminal_confirms(seeded, monkeypatch, capsys):
    _tty(monkeypatch)
    assert cli.main(["relations", "confirm", seeded["rel_id"]]) == 0
    state = relations.records(project_dir=PROJECT)[seeded["rel_id"]]["state"]
    assert state == "confirmed"


def test_reject_and_retract_share_the_gate(seeded, monkeypatch):
    _tty(monkeypatch)
    assert cli.main(["relations", "reject", seeded["rel_id"]]) == 0
    assert cli.main(["relations", "confirm", seeded["rel_id"]]) == 0
    assert cli.main(["relations", "retract", seeded["rel_id"]]) == 0
    state = relations.records(project_dir=PROJECT)[seeded["rel_id"]]["state"]
    assert state == "retracted"


def test_verdict_on_unknown_relation_fails_cleanly(seeded, monkeypatch,
                                                   capsys):
    _tty(monkeypatch)
    assert cli.main(["relations", "confirm", "rel-" + "f" * 16]) == 1


def test_list_withholds_edges_touching_erased_endpoints(seeded, capsys):
    doomed = seeded["item_ids"][0]
    store.append_event(doomed, "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=PROJECT)
    assert cli.main(["relations", "list"]) == 0
    out = capsys.readouterr().out
    assert seeded["rel_id"] not in out
    assert "withheld" in out
