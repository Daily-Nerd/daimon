"""PR 13 (D6): every cache that holds a judgement of the machine-wide sets
learns the teammates' quarantine file: the forgotten stamp (and so the judge
memo), the recall fingerprint, the unproven-author record and the machine
census. The teammate's checkpoint and published file are planted by hand: this
is what a pulled sidecar looks like to the reader."""

import json
import sqlite3

import pytest

from daimon_briefing import (config, ledger_census, normalize, recall, schema,
                             store, trust, view)

OWN = "/p/team-caches"
TEXT = "the quokkateam claim was fabricated by the model"
KEY = trust.value_key(TEXT)
OTHER = "a plain decision about the walrusteam deployment"
DECISION = next(f for f in schema.ITEM_FIELDS if f.kind == "decision")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    recall._forced_at.clear()
    yield
    recall._forced_at.clear()


def _teammate(monkeypatch, texts=(TEXT, OTHER), author="grace"):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/x")
    remote = config.team_dir() / "team-a"
    (remote / ".git").mkdir(parents=True, exist_ok=True)
    adir = remote / "projects" / "core" / "x" / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "S1.json").write_text(json.dumps({
        "session_id": "S1", "created": "2026-10-01T00:00:00Z",
        "author": author, "team_project": "core/x",
        "project_slug": store.project_slug(OWN),
        "working_context": {"recent_decisions": [
            {"text": t, "trust": "inferred"} for t in texts]},
    }), encoding="utf-8")
    return adir


def _row(state="active", *, order=1, event_id="e1", key=KEY):
    return {"version": 1, "ts": "2026-10-09T12:00:00Z", "order": order,
            "event_id": event_id, "quarantine_id": "tr-0123456789ab",
            "kind": "decision", "value_key": key, "state": state,
            "author": "grace"}


def _publish(adir, *rows):
    with open(adir / "quarantines.jsonl", "ab") as handle:
        for row in rows:
            handle.write(json.dumps(row).encode() + b"\n")


def _texts():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return {r[0] for r in conn.execute("SELECT text FROM items")}
    finally:
        conn.close()


def _meta():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()


# ---- the forgotten stamp and the judge memo ---------------------------------


def test_the_stamp_follows_a_foreign_quarantine_file(tmp_checkpoint_dir,
                                                     monkeypatch):
    adir = _teammate(monkeypatch)
    before = store.forgotten_stamp()
    _publish(adir, _row())
    active = store.forgotten_stamp()
    assert active != before
    _publish(adir, _row("released", order=2, event_id="e2"))
    assert store.forgotten_stamp() not in (before, active)


def test_the_stamp_includes_the_own_file_and_never_resolves_the_author(
        tmp_checkpoint_dir, monkeypatch):
    own = config.team_dir() / "team-a" / "authors" / "ada"
    own.mkdir(parents=True)
    ledger = own / "quarantines.jsonl"
    ledger.write_text("")

    def boom():
        raise AssertionError("config.author() forks git config")

    monkeypatch.setattr(config, "author", boom)
    assert str(ledger) in repr(store.forgotten_stamp())


def test_the_stamp_skips_the_local_mirror(tmp_checkpoint_dir):
    local = config.team_dir() / "local" / "authors" / "grace"
    local.mkdir(parents=True)
    (local / "quarantines.jsonl").write_text("")
    assert "quarantines.jsonl" not in repr(store.forgotten_stamp())


def test_a_warm_judge_drops_when_a_foreign_quarantine_is_pulled_released_and_rewritten(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": TEXT, "id": "d-aaaaaa"}]}, "epistemic_snapshot": {}}
    from daimon_briefing.surfaces import Writer
    store.write_checkpoint("S-1", cp, project_dir=OWN, writer=Writer.HUMAN)
    slug = store.project_slug(OWN)
    item = {"text": TEXT, "id": "d-aaaaaa"}
    assert isinstance(view.judge(slug).verdict(DECISION, item), view.Visible)
    _publish(adir, _row())
    assert isinstance(view.judge(slug).verdict(DECISION, item), view.Withheld)
    _publish(adir, _row("released", order=2, event_id="e2"))
    assert isinstance(view.judge(slug).verdict(DECISION, item), view.Visible)
    (adir / "quarantines.jsonl").write_bytes(
        json.dumps(_row(order=5, event_id="e5")).encode() + b"\n")
    assert isinstance(view.judge(slug).verdict(DECISION, item), view.Withheld)


# ---- the recall fingerprint and the index -------------------------------------


def test_the_fingerprint_changes_on_a_pulled_file_and_a_pulled_release(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    base = recall._fingerprint()
    _publish(adir, _row())
    active = recall._fingerprint()
    assert active != base
    _publish(adir, _row("released", order=2, event_id="e2"))
    assert recall._fingerprint() not in (base, active)


def test_the_fingerprint_sees_the_local_mirror_and_the_own_file(
        tmp_checkpoint_dir):
    base = recall._fingerprint()
    own = config.team_dir() / "local" / "authors" / "ada"
    own.mkdir(parents=True)
    (own / "quarantines.jsonl").write_text("x\n")
    assert recall._fingerprint() != base


def test_an_activation_removes_the_row_and_a_release_restores_it(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    recall.rebuild()
    assert TEXT in _texts() and OTHER in _texts()
    _publish(adir, _row())
    recall.rebuild()
    assert TEXT not in _texts() and OTHER in _texts()
    blob = config.recall_db().read_bytes()
    assert b"quokkateam" not in blob and b"walrusteam" in blob
    _publish(adir, _row("released", order=2, event_id="e2"))
    recall.rebuild()
    assert TEXT in _texts()


def test_a_query_before_the_rebuild_still_drops_the_row(tmp_checkpoint_dir,
                                                        monkeypatch):
    adir = _teammate(monkeypatch)
    recall.rebuild()
    assert TEXT in _texts()
    _publish(adir, _row())
    got = recall.query("quokkateam", all_projects=True)
    assert [r["text"] for r in got.rows] == []
    assert TEXT not in _texts()          # the dropped row forced the rebuild


def test_unproven_authors_records_an_author_skipped_for_the_quarantine_file_only(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(b"")
    recall.rebuild()
    assert json.loads(_meta()["unproven_authors"]) == []
    assert TEXT in _texts()
    (adir / "quarantines.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    recall.rebuild()
    assert json.loads(_meta()["unproven_authors"]) == ["grace"]
    assert _texts() == set()
    got = recall.query("walrusteam", all_projects=True)
    assert "author-skipped" in got.notes


def test_a_newly_unproven_quarantine_file_is_noticed_by_the_query(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    recall.rebuild()
    assert OTHER in _texts()
    (adir / "quarantines.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    got = recall.query("walrusteam", all_projects=True)
    assert got.rows == [] and "author-skipped" in got.notes


# ---- the machine census ------------------------------------------------------


def test_the_machine_census_reports_both_team_ledgers(tmp_checkpoint_dir):
    adir = config.team_dir() / "remote-a" / "authors" / "grace"
    adir.mkdir(parents=True)
    (adir / "tombstones.jsonl").write_bytes(
        json.dumps({"key": "ab" * 8}).encode() + b"\n")
    (adir / "quarantines.jsonl").write_bytes(
        json.dumps(_row()).encode() + b"\n" + b'{"torn": ')
    result = ledger_census.census_machine()
    assert result["team/remote-a/authors/grace/tombstones.jsonl"]["state"] == "ok"
    assert (result["team/remote-a/authors/grace/quarantines.jsonl"]["state"]
            == "degraded")


def test_normalize_is_what_value_keys_are(tmp_checkpoint_dir):
    assert KEY == normalize.content_key(TEXT)
