"""A verb that looks a record up before it writes judges its ledger first
(#1132 PR 10b fix round): an unproven ledger is refused (exit 2 through the
central handler) and never answered "unknown id"."""

import pytest

from daimon_briefing import cli, store

PROJECT = "/p/verb-preflight"
RID, AID, QID, REL = ("r-aaaaaaaaaaaa", "a-aaaaaaaaaaaa", "q-aaaaaaaaaaaa",
                      "rel-aaaaaaaaaaaaaaaa")
ITEM = "o-aaaaaaaaaaaa"

VERBS = [
    ("refutations.jsonl", ["refute", "ratify", RID]),
    ("refutations.jsonl", ["refute", "revise", RID, "--evidence", "x:1"]),
    ("refutations.jsonl", ["refute", "overturn", RID, "--evidence", "x:1"]),
    ("refutations.jsonl", ["ruling", "ratify", RID]),
    ("refutations.jsonl", ["ruling", "revise", RID, "--evidence", "x:1"]),
    ("refutations.jsonl", ["ruling", "retire", RID]),
    ("amendments.jsonl", ["amend", "ratify", AID]),
    ("amendments.jsonl", ["amend", "reject", AID]),
    ("amendments.jsonl", ["amend", "propose", ITEM, "--change", "progressed",
                          "--evidence", "x"]),
    ("events.jsonl", ["resolve", ITEM]),
    ("events.jsonl", ["resolve", ITEM, "--by", "agent", "--evidence", "x"]),
    ("events.jsonl", ["reverify", ITEM, "--evidence", "x"]),
    ("requests.jsonl", ["request", "open", "--to=-p-other", "--ask", "a",
                        "--why", "b", "--anyway"]),
    ("requests.jsonl", ["request", "revise", QID]),
    ("requests.jsonl", ["request", "accept", QID]),
    ("requests.jsonl", ["request", "reject", QID]),
    ("requests.jsonl", ["request", "needs-info", QID]),
    ("requests.jsonl", ["request", "suppress", QID]),
    ("requests.jsonl", ["request", "done", QID, "--evidence", "x"]),
    ("requests.jsonl", ["request", "reply", QID, "--note", "x"]),
    ("relations.jsonl", ["relations", "confirm", REL]),
    ("relations.jsonl", ["relations", "reject", REL]),
    ("relations.jsonl", ["relations", "retract", REL]),
]


@pytest.mark.parametrize("ledger,argv", VERBS,
                         ids=[" ".join(v[1][:2]) + (" agent" if "agent" in v[1] else "")
                              for v in VERBS])
def test_a_lookup_verb_is_refused_on_an_unproven_ledger(
        tmp_checkpoint_dir, capsys, monkeypatch, ledger, argv):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    path = store._events_path(store._resolved(PROJECT)).parent / ledger
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc = cli.main([*argv, "--project", PROJECT])
    out = capsys.readouterr()
    assert rc == 2, (argv, out)
    assert f"{ledger} is unreadable" in out.err
    assert "unknown" not in (out.out + out.err).lower()
    assert path.read_bytes() == b"<<<<<<< conflict\n"


def test_a_resolve_dry_run_writes_nothing_and_needs_no_proven_ledger(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    path = store._events_path(store._resolved(PROJECT)).parent / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc = cli.main(["resolve", ITEM, "--dry-run", "--project", PROJECT])
    out = capsys.readouterr()
    assert rc != 2 and "is unreadable" not in out.err
    assert path.read_bytes() == b"<<<<<<< conflict\n"
