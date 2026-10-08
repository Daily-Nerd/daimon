"""Fixes from the blind review of PR 9a (#1132): the listing cost, the
transient trust ledger, the tombstone predicate, notes after a forced rebuild,
the tmp file on a failed connect and the uniform failure of a recall host."""

import json
import os
import sqlite3

import pytest

from daimon_briefing import (cli, config, jsonl, mcp_tools, recall,
                             store, trust, view)
from daimon_briefing.jsonl import Health

HOT = "walrusharbor"
VISIBLE = "a plain decision about the walrusharbor deployment"
QUARANTINED = "the quokkasentinel claim was fabricated by the model"


def _cp(sid, decisions):
    return {"session_id": sid, "created": "2026-08-01T00:00:00Z",
            "working_context": {"recent_decisions": decisions,
                                "open_questions": []},
            "epistemic_snapshot": {}}


def _write(project, texts, sid="S1"):
    store.write_checkpoint(
        sid, _cp(sid, [{"text": t, "trust": "inferred"} for t in texts]),
        project_dir=project)
    return store.project_slug(project)


def _quarantine(project, text):
    return trust.propose(text=text, kind="decision", reason="fabricated",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


def _texts():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return {r[0] for r in conn.execute("SELECT text FROM items")}
    finally:
        conn.close()


def _closed_meta():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return json.loads(dict(conn.execute("SELECT key, value FROM meta"))
                          ["closed"])
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _limiter():
    recall._forced_at.clear()
    yield
    recall._forced_at.clear()


# ---- 1: the listing pays the forgotten stamp once --------------------------


def test_projects_takes_the_forgotten_stamp_and_set_once(tmp_checkpoint_dir,
                                                         monkeypatch):
    for i in range(6):
        _write(f"/p/many-{i}", [f"decision number {i}"])
    stamps, foreign = [], []
    real_stamp = store.forgotten_stamp
    real_foreign = store.foreign_forgotten_content_keys
    monkeypatch.setattr(store, "forgotten_stamp",
                        lambda: stamps.append(1) or real_stamp())
    monkeypatch.setattr(store, "foreign_forgotten_content_keys",
                        lambda: foreign.append(1) or real_foreign())
    listed = view.projects(None)
    assert len(listed) == 6
    assert len(stamps) == 1
    assert len(foreign) == 1


def test_the_forgotten_stamp_never_resolves_the_author(tmp_checkpoint_dir,
                                                       monkeypatch):
    ledger = (config.team_dir() / "remote" / "authors" / "someone"
              / "tombstones.jsonl")
    ledger.parent.mkdir(parents=True)
    ledger.write_text("")

    def boom():
        raise AssertionError("config.author() forks git config")

    monkeypatch.setattr(config, "author", boom)
    assert str(ledger) in repr(store.forgotten_stamp())


# ---- 2: a trust ledger that reads TRANSIENT closes the bucket ---------------


def _transient_trust(monkeypatch):
    real = jsonl.read

    def read(path, *a, **k):
        if path.name == "trust.jsonl":
            return jsonl.Read(Health.TRANSIENT, [], detail="EBUSY")
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)
    return real


def test_a_transient_trust_read_closes_the_bucket_at_build(
        tmp_checkpoint_dir, monkeypatch):
    project = "/repo/transient"
    slug = _write(project, [VISIBLE, QUARANTINED])
    _quarantine(project, QUARANTINED)
    real = _transient_trust(monkeypatch)
    recall.rebuild()
    assert _texts() == set()
    assert _closed_meta() == [slug]
    assert b"quokkasentinel" not in config.recall_db().read_bytes()
    monkeypatch.setattr(jsonl, "read", real)
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    got = recall.query(HOT, project_dir=project)
    assert [r["text"] for r in got.rows] == [VISIBLE]


# The strict xfail that stood here (an unreadable events ledger empties the
# machine-wide forgotten set) is replaced by the two tests in
# tests/test_forgotten_incomplete.py (#1132 PR 10a, D10.2).


# ---- 4: the id rule uses the tombstone predicate ----------------------------


def test_a_free_form_status_is_not_a_tombstone_for_the_id_rule(
        tmp_checkpoint_dir):
    slug = _write("/p/ff", ["kept words here"])
    got = store.read_latest_body(project_dir="/p/ff", route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    item_id = got["working_context"]["recent_decisions"][0]["id"]
    assert store.append_event(item_id, "forgotten about it",
                              project_dir="/p/ff")
    assert view.judge(slug).snap.forgotten_ids == frozenset()
    kept = view.open(slug, live=False).checkpoint
    assert [d["text"] for d in
            kept["working_context"]["recent_decisions"]] == ["kept words here"]


def test_a_real_tombstone_withholds_by_id(tmp_checkpoint_dir):
    slug = _write("/p/rt", ["kept words here"])
    store.append_event("d-aaaaaa", "forgotten:" + "0" * 16,
                       kind="tombstone", tombstone=True, project_dir="/p/rt")
    assert view.judge(slug).snap.forgotten_ids == frozenset({"d-aaaaaa"})


def test_the_index_marks_a_free_form_forgotten_status_as_resolved(
        tmp_checkpoint_dir):
    _write("/p/ffi", [VISIBLE])
    got = store.read_latest_body(project_dir="/p/ffi", route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    item_id = got["working_context"]["recent_decisions"][0]["id"]
    store.append_event(item_id, "forgotten about it", project_dir="/p/ffi")
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        row = conn.execute("SELECT superseded_by, superseded_source"
                           " FROM items").fetchone()
    finally:
        conn.close()
    assert row == ("resolved", "resolution")


# ---- 5: notes after a forced rebuild ---------------------------------------


def test_a_skipped_forced_rebuild_for_a_reopened_bucket_says_stale(
        tmp_checkpoint_dir, monkeypatch):
    project = "/repo/skip"
    slug = _write(project, [VISIBLE])
    ledger = config.checkpoint_dir() / slug / "trust.jsonl"
    ledger.write_bytes(b"not json\n")
    recall.rebuild()
    ledger.write_bytes(b"")
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    monkeypatch.setattr(recall, "_monotonic", lambda: 100.0)
    recall._forced_at[str(config.recall_db())] = 99.0   # inside the window
    got = recall.query(HOT, project_dir=project)
    assert got.rows == []
    assert "stale" in got.notes


def test_a_bucket_that_closes_under_a_query_is_noted_after_the_rebuild(
        tmp_checkpoint_dir, monkeypatch):
    project = "/repo/closes"
    slug = _write(project, [VISIBLE])
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    (config.checkpoint_dir() / slug / "trust.jsonl").write_bytes(b"junk\n")
    got = recall.query(HOT, project_dir=project)
    assert got.rows == []
    assert "closed" in got.notes


# ---- 6: a failed connect leaves no tmp file ---------------------------------


def test_a_failed_connect_removes_the_staging_file(tmp_checkpoint_dir,
                                                   monkeypatch):
    _write("/repo/conn", [VISIBLE])

    def boom(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(recall.sqlite3, "connect", boom)
    with pytest.raises(sqlite3.OperationalError):
        recall.rebuild()
    db = config.recall_db()
    assert [p.name for p in db.parent.glob(db.name + ".*")] == []


def test_the_finished_index_is_private(tmp_checkpoint_dir):
    _write("/repo/mode", [VISIBLE])
    recall.rebuild()
    assert os.stat(config.recall_db()).st_mode & 0o777 == 0o600


# ---- 7: a judge that raises fails the hosts closed --------------------------


def _boom_judge(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("view.judge failed")

    monkeypatch.setattr(view, "judge", boom)


def test_cli_recall_fails_closed_with_one_line_when_the_judge_raises(
        tmp_checkpoint_dir, monkeypatch, capsys):
    project = "/repo/hostfail"
    _write(project, [VISIBLE])
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    _boom_judge(monkeypatch)
    assert cli.main(["recall", HOT]) == 2
    out = capsys.readouterr()
    assert VISIBLE not in out.out + out.err
    assert out.out == ""
    assert out.err.strip().count("\n") == 0 and "Traceback" not in out.err


def test_mcp_recall_raises_a_tool_error_when_the_judge_raises(
        tmp_checkpoint_dir, monkeypatch):
    project = "/repo/hostfail2"
    _write(project, [VISIBLE])
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    _boom_judge(monkeypatch)
    with pytest.raises(mcp_tools.ToolError) as caught:
        mcp_tools.HANDLERS["daimon_recall"]({"query": HOT})
    assert VISIBLE not in str(caught.value)
