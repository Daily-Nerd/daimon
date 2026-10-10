"""PR 13 (D6): small worlds for the cases the shared sentinel world cannot hold.

Every quarantine a teammate publishes here comes out of the real writer
(`trust.propose` and `trust.release` as that author, on a human channel), and
ada reads it through the real readers. The only hand-written bytes are the
ones a case is ABOUT: a forged row, a torn tail, an unreadable file. They go
in with an independent byte writer and the case says so (scar 0096)."""

import json
import sqlite3

import pytest

from daimon_briefing import (cli, config, normalize, recall, schema, store,
                             trust, view)
from daimon_briefing.surfaces import Writer

TEXT = "SENTINEL-teamworld the signing seed lives under the stairs"
OTHER = "an unrelated decision stays visible in the team world"
DECISION = next(f for f in schema.ITEM_FIELDS if f.kind == "decision")
BELIEF = next(f for f in schema.ITEM_FIELDS if f.kind == "belief")
KEY = trust.value_key(TEXT)


class TeamWorld:
    def __init__(self, tmp_path, monkeypatch):
        self.mp = monkeypatch
        self.tmp = tmp_path
        for name in ("DAIMON_CHECKPOINT_DIR", "DAIMON_TEAM_DIR",
                     "DAIMON_LOG_DIR", "DAIMON_RECALL_DB", "DAIMON_ENV_FILE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("DAIMON_TEAM", "1")
        monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/census")
        monkeypatch.setenv("DAIMON_AUTHOR", "ada")
        self.sidecar = config.team_dir() / "team-a"
        (self.sidecar / ".git").mkdir(parents=True)
        self.own = str(tmp_path / "ada-proj")
        self.peer = str(tmp_path / "peer-proj")
        recall._forced_at.clear()

    def as_author(self, name):
        self.mp.setenv("DAIMON_AUTHOR", name)

    def adir(self, author):
        return (self.sidecar / "projects" / "squad" / "census" / "authors"
                / author)

    def checkpoint(self, author="ada", sid="S-1"):
        """A checkpoint of `author`'s holding the quarantined value and a
        plain one, written by the real writer (and mirrored into the sidecar
        by it)."""
        self.as_author(author)
        cp = {"session_id": sid, "created": "2026-08-01T00:00:00Z",
              "working_context": {"recent_decisions": [
                  {"text": TEXT, "trust": "inferred"},
                  {"text": OTHER, "trust": "inferred"}]},
              "epistemic_snapshot": {}}
        store.write_checkpoint(sid, cp, project_dir=self.own,
                               writer=Writer.HUMAN)
        self.as_author("ada")

    def project_of(self, author):
        """Each teammate quarantines from a project of their own, so ada's
        bucket never holds the record and two authors never share an id."""
        return self.peer if author == "grace" else str(
            self.tmp / f"{author}-proj")

    def quarantine(self, author="grace", text=TEXT):
        self.as_author(author)
        try:
            return trust.propose(text=text, kind="decision", reason="theirs",
                                 evidence=["issue:1"], channel="cli-tty",
                                 project_dir=self.project_of(author))
        finally:
            self.as_author("ada")

    def release(self, tid, author="grace"):
        self.as_author(author)
        try:
            trust.release(tid, channel="cli-tty",
                          project_dir=self.project_of(author))
        finally:
            self.as_author("ada")

    def slug(self):
        return store.project_slug(self.own)

    def verdict(self, text=TEXT, fld=DECISION):
        item = {"text": text, "id": "d-aaaaaa"}
        return view.classify(fld, item, view.snapshot(self.slug()))


@pytest.fixture
def team(tmp_path, monkeypatch):
    return TeamWorld(tmp_path, monkeypatch)


def _index_texts():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return {r[0] for r in conn.execute("SELECT text FROM items")}
    finally:
        conn.close()


def _teammates(team):
    return [a for a, _cp in store.read_team(project_dir=team.own)
            if a != "ada"]


def _teammate_checkpoint(team):
    """grace's own mirrored checkpoint, written by the real writer as grace."""
    team.checkpoint("grace", "G-1")


# ---- the happy path, through real writers on both sides ------------------------


def test_grace_quarantines_and_ada_withholds_everywhere(team, capsys):
    team.checkpoint()
    _teammate_checkpoint(team)
    assert isinstance(team.verdict(), view.Visible)
    tid = team.quarantine()
    assert team.adir("grace").joinpath("quarantines.jsonl").exists()
    verdict = team.verdict()
    assert isinstance(verdict, view.Withheld) and verdict.quarantine_id is None
    # the positive controls: another value, and the same text as another kind
    assert isinstance(team.verdict(OTHER), view.Visible)
    assert isinstance(team.verdict(TEXT, BELIEF), view.Visible)
    # ada's own bucket never holds grace's record
    assert trust.get(tid, project_dir=team.own) is None
    # the briefing and the team view say nothing of the value
    capsys.readouterr()
    assert cli.main(["brief", "--project", team.own]) == 0
    assert cli.main(["brief", "--team", "--project", team.own]) == 0
    out = capsys.readouterr().out
    assert "SENTINEL-teamworld" not in out and OTHER in out
    # and so does the recall index, in the rows and in the bytes
    recall.rebuild()
    assert TEXT not in _index_texts() and OTHER in _index_texts()
    assert b"SENTINEL-teamworld" not in config.recall_db().read_bytes()


def test_the_authors_own_pairs_are_not_foreign(team):
    team.checkpoint()
    mine = trust.propose(text="a value ada quarantined here in the own project",
                         kind="decision", reason="mine", evidence=["issue:1"],
                         channel="cli-tty", project_dir=team.own)
    team.quarantine()
    assert team.adir("ada").joinpath("quarantines.jsonl").exists()
    foreign = store.foreign_quarantines()
    assert foreign == {("decision", KEY)}
    assert ("decision", trust.value_key(
        "a value ada quarantined here in the own project")) not in foreign
    assert trust.get(mine, project_dir=team.own)["state"] == "active"


def test_a_release_lifts_the_claim_and_the_index_gets_the_row_back(team):
    team.checkpoint()
    _teammate_checkpoint(team)
    tid = team.quarantine()
    recall.rebuild()
    assert TEXT not in _index_texts()
    before = recall._fingerprint()
    team.release(tid)
    assert recall._fingerprint() != before
    assert isinstance(team.verdict(), view.Visible)
    recall.rebuild()
    assert TEXT in _index_texts()


def test_a_same_key_conflict_keeps_the_value_withheld(team):
    team.checkpoint()
    team.quarantine("grace")
    kay = team.quarantine("kay")
    team.release(kay, "kay")
    # kay retracted only kay's own claim; grace's stands (union, fail-safe)
    assert isinstance(team.verdict(), view.Withheld)
    recall.rebuild()
    assert TEXT not in _index_texts()


def test_a_quarantine_made_in_two_projects_survives_one_release(team):
    team.checkpoint()
    first = team.quarantine("grace")
    other_peer = str(team.tmp / "peer-two")
    team.as_author("grace")
    second = trust.propose(text=TEXT, kind="decision", reason="again",
                           evidence=["issue:2"], channel="cli-tty",
                           project_dir=other_peer)
    team.as_author("ada")
    assert first != second           # ids hash the project slug
    team.release(first)
    assert isinstance(team.verdict(), view.Withheld)


# ---- hand-written bytes: the cases that are ABOUT the bytes ---------------------


def test_a_planted_release_lifts_the_claim_by_design(team):
    """R3.1, deliberate and documented: nothing authenticates who appended to
    `authors/<owner>/quarantines.jsonl`. Sidecar write access is the trust
    boundary, so a member who can write the owner's file can lift the owner's
    claim with a `released` row (deleting its lines does the same, and already
    re-exposes a forgotten value today). The claim comes from the real writer;
    the forged row goes in with a plain byte append, here and only here."""
    team.checkpoint()
    team.quarantine("grace")
    assert isinstance(team.verdict(), view.Withheld)
    path = team.adir("grace") / "quarantines.jsonl"
    rows = [json.loads(ln) for ln in path.read_text().splitlines()]
    forged = dict(rows[-1], state="released", order=rows[-1]["order"] + 1,
                  event_id="f" * 32, author="mallory")
    with open(path, "ab") as handle:        # independent byte writer
        handle.write(json.dumps(forged).encode() + b"\n")
    assert isinstance(team.verdict(), view.Visible)


def test_an_unreadable_quarantine_file_skips_the_author(team, capsys):
    team.checkpoint()
    _teammate_checkpoint(team)
    team.quarantine("grace")
    with open(team.adir("grace") / "quarantines.jsonl", "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")    # a merge marker: UNREADABLE
    notes = view.team_notes()
    assert any("cannot be read" in n for n in notes)
    # grace's checkpoints are not admitted, by either reader
    assert _teammates(team) == []
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        recorded = dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()
    assert json.loads(recorded["unproven_authors"]) == ["grace"]
    # the good line published before the garbage still withholds
    assert isinstance(team.verdict(), view.Withheld)
    capsys.readouterr()
    assert cli.main(["brief", "--team", "--project", team.own]) == 0
    assert "SENTINEL-teamworld" not in capsys.readouterr().out


def test_a_torn_tail_skips_the_author_the_same_way(team):
    team.checkpoint()
    _teammate_checkpoint(team)
    team.quarantine("grace")
    with open(team.adir("grace") / "quarantines.jsonl", "ab") as handle:
        handle.write(b'{"version": 1, "state": "rel')   # the newest claim
    assert any("cannot be read" in n for n in view.team_notes())
    assert _teammates(team) == []
    recall.rebuild()
    assert b"SENTINEL-teamworld" not in config.recall_db().read_bytes()


def test_an_unreadable_tombstone_file_skips_the_author_for_both_ledgers(team):
    team.checkpoint()
    _teammate_checkpoint(team)
    team.quarantine("grace")
    (team.adir("grace") / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert store.foreign_team().unproven == {"grace"}
    assert _teammates(team) == []
    assert isinstance(team.verdict(), view.Withheld)


def test_nothing_reaches_the_index_from_an_unproven_author(team):
    team.checkpoint()
    _teammate_checkpoint(team)
    team.quarantine("grace")
    (team.adir("grace") / "quarantines.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    recall.rebuild()
    assert normalize.content_key(TEXT) not in repr(_index_texts())


# ---- fix round 1: one poisoned file never un-hides another author's claims ----


def test_a_poisoned_file_leaves_every_other_claim_in_force(team, capsys,
                                                           monkeypatch):
    """Author mallory's file holds rows with `order` Infinity, NaN and 1e999
    (valid JSON to Python), planted with an independent byte writer. grace's
    real quarantine and real tombstone both stay in force on every reader,
    and nothing raises. The poisoned rows themselves fold as order 0, so
    mallory is not skipped (the file is readable); a file whose fold raises
    is, which test_foreign_quarantines pins."""
    forgotten = "SENTINEL-teamworld a value grace forgot elsewhere"
    team.checkpoint()
    team.as_author("grace")
    team.mp.setenv("DAIMON_TEAM_PROJECT", "squad/census")
    tid = trust.propose(text=TEXT, kind="decision", reason="theirs",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=team.project_of("grace"))
    assert tid.published
    assert store.publish_tombstone(normalize.content_key(forgotten),
                                   project_dir=team.project_of("grace"))
    team.as_author("ada")
    mallory = team.adir("mallory")
    mallory.mkdir(parents=True)
    with open(mallory / "quarantines.jsonl", "ab") as handle:  # byte writer
        for literal in (b"Infinity", b"-Infinity", b"NaN", b"1e999"):
            handle.write(b'{"kind":"decision","value_key":"abcdef0123456789",'
                         b'"state":"active","order":' + literal + b"}\n")
    cp = {"session_id": "S-9", "created": "2026-08-02T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": forgotten, "trust": "inferred"},
              {"text": TEXT, "trust": "inferred"},
              {"text": OTHER, "trust": "inferred"}]},
          "epistemic_snapshot": {}}
    store.write_checkpoint("S-9", cp, project_dir=team.own,
                           writer=Writer.HUMAN)
    capsys.readouterr()
    for argv in (["brief"], ["brief", "--team"]):
        assert cli.main([*argv, "--project", team.own]) == 0
        out = capsys.readouterr().out
        assert "SENTINEL-teamworld" not in out and OTHER in out, argv
    recall.rebuild()
    texts = " ".join(_index_texts())
    assert "SENTINEL-teamworld" not in texts and OTHER in texts
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["trust", "list", "--team", "--project", team.own]) == 0
    out = capsys.readouterr().out
    assert "decision" in out and "\x1b" not in out
    assert ("decision", "abcdef0123456789") in store.foreign_quarantines()
    assert store.foreign_team().unproven == frozenset()
