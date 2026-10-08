"""The machine-wide forget set knows when it may be incomplete
(#1132 PR 10a, D10.2).

One bucket's events ledger that cannot be read used to shrink the set for
every other bucket in silence. Now `store.forgotten_incomplete()` names it and
every read surface says so; the reader of that bucket's own recall index is
closed (`Judge.index_closed`), a reader of any other bucket is not.
"""

import io
import json
import sqlite3

import pytest

from daimon_briefing import (cli, config, jsonl, mcp_tools, normalize, recall,
                             store, view)
from daimon_briefing.jsonl import Health

FORGETFUL = "/repo/forgetful"
OTHER = "/repo/other"
STILL_HERE = "the pangolinsentinel decision stays"
NOTE = ("⚠ the forget set is incomplete: an events ledger cannot be read; "
        "run: daimon status")


def _cp(sid, texts):
    return {"session_id": sid, "created": "2026-08-01T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": t, "trust": "inferred"} for t in texts],
                "open_questions": []},
            "epistemic_snapshot": {}}


def _write(project, texts, sid=None):
    # One session file per project: two projects sharing a session id would
    # overwrite each other's flat copy.
    sid = sid or "S-" + store.project_slug(project).strip("-")
    store.write_checkpoint(sid, _cp(sid, texts), project_dir=project)
    return store.project_slug(project)


def _forget(project, text):
    store.append_event("d-aaaaaa", "forgotten:" + normalize.content_key(text),
                       kind="tombstone", tombstone=True, project_dir=project)


def _events(project):
    return config.checkpoint_dir() / store.project_slug(project) / "events.jsonl"


def _seam(monkeypatch, project, result=None):
    result = result or jsonl.Read(Health.UNREADABLE, [], detail="EIO")
    real = jsonl.read
    target = _events(project)

    def read(path, *a, **k):
        return result if path == target else real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)


@pytest.fixture(autouse=True)
def _limiter():
    recall._forced_at.clear()
    yield


# ---- the store -------------------------------------------------------------


def test_a_healthy_store_has_nothing_incomplete(tmp_checkpoint_dir):
    _write(FORGETFUL, ["x is the forgotten words here"])
    _forget(FORGETFUL, STILL_HERE)
    assert store.forgotten_incomplete() == frozenset()
    assert normalize.content_key(STILL_HERE) in store.all_forgotten_content_keys()


def test_a_garbage_events_ledger_is_named(tmp_checkpoint_dir):
    slug = _write(FORGETFUL, ["words"])
    _write(OTHER, ["other words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    assert store.forgotten_incomplete() == frozenset({slug})


def test_an_os_error_and_a_transient_ledger_are_named(tmp_checkpoint_dir,
                                                      monkeypatch):
    slug = _write(FORGETFUL, ["words"])
    _seam(monkeypatch, FORGETFUL)
    assert store.forgotten_incomplete() == frozenset({slug})
    _seam(monkeypatch, FORGETFUL, jsonl.Read(Health.TRANSIENT, [],
                                              detail="EBUSY"))
    assert store.forgotten_incomplete() == frozenset({slug})


def test_a_degraded_events_ledger_is_proven(tmp_checkpoint_dir):
    _write(FORGETFUL, ["words"])
    _forget(FORGETFUL, STILL_HERE)
    with open(_events(FORGETFUL), "ab") as handle:
        handle.write(b'{"kind": "resolution", "item_ref": "o-bb')
    assert store.forgotten_incomplete() == frozenset()
    assert normalize.content_key(STILL_HERE) in store.all_forgotten_content_keys()


def test_the_good_lines_of_a_broken_bucket_still_feed_the_set(
        tmp_checkpoint_dir):
    _write(FORGETFUL, ["words"])
    _forget(FORGETFUL, STILL_HERE)
    with open(_events(FORGETFUL), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    assert normalize.content_key(STILL_HERE) in store.all_forgotten_content_keys()
    assert store.forgotten_incomplete()


def test_the_walk_is_never_memoized_while_a_ledger_is_unproven(
        tmp_checkpoint_dir, monkeypatch):
    _write(FORGETFUL, ["words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    reads = []
    real = jsonl.read

    def spy(path, *a, **k):
        if path.name == "events.jsonl":
            reads.append(path)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", spy)
    store.forgotten_incomplete()
    store.forgotten_incomplete()
    assert len(reads) == 2
    _events(FORGETFUL).write_bytes(b"")          # repaired
    reads.clear()
    assert store.forgotten_incomplete() == frozenset()
    store.forgotten_incomplete()
    assert len(reads) == 1                        # memoized again


def test_view_forgotten_notes_is_the_one_line(tmp_checkpoint_dir):
    assert view.forgotten_notes() == ()
    _write(FORGETFUL, ["words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    assert view.forgotten_notes() == (NOTE,)


def test_a_snapshot_of_another_bucket_carries_the_line(tmp_checkpoint_dir):
    _write(FORGETFUL, ["words"])
    _write(OTHER, ["other words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    snap = view.snapshot(OTHER)
    assert snap.notes() == (NOTE,)
    assert snap.closed is False
    assert view.suppressed(OTHER, 0.0).notes == (NOTE,)


def test_the_projects_listing_carries_the_line(tmp_checkpoint_dir):
    _write(FORGETFUL, ["words"])
    _write(OTHER, ["other words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    assert view.projects_notes(None) == (NOTE,)


# ---- the two replacements of the strict xfail ------------------------------


def _closed_meta():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return json.loads(dict(conn.execute("SELECT key, value FROM meta"))
                          ["closed"])
    finally:
        conn.close()


def _texts():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return {r[0] for r in conn.execute("SELECT text FROM items")}
    finally:
        conn.close()


def test_an_unreadable_own_events_ledger_closes_the_recall_index_only(
        tmp_checkpoint_dir, monkeypatch, capsys):
    slug = _write(FORGETFUL, ["the walrusharbor deployment decision"])
    _seam(monkeypatch, FORGETFUL)
    judge = view.judge(slug)
    assert judge.index_closed is True and judge.closed is False
    recall.rebuild()
    assert _texts() == set()
    assert _closed_meta() == [slug]
    # why, the viewer and the briefing keep reading the bucket (decision 1).
    assert view.open(slug, live=False).checkpoint is not None
    got = recall.query("walrusharbor", project_dir=FORGETFUL)
    assert got.rows == []
    assert "closed" in got.notes and "forget-incomplete" in got.notes
    # CLI, MCP and the viewer word it the same way.
    assert cli.main(["recall", "walrusharbor", "--project", FORGETFUL]) == 0
    out = capsys.readouterr().out
    assert "forget set is incomplete" in out
    monkeypatch.setenv("DAIMON_PROJECT_DIR", FORGETFUL)
    tool = mcp_tools.HANDLERS["daimon_recall"]({"query": "walrusharbor"})
    assert json.loads(tool.text) == []
    assert any("forget set is incomplete" in n for n in tool.notes)


def test_only_a_foreign_events_ledger_broken_does_not_close_this_bucket(
        tmp_checkpoint_dir, monkeypatch, capsys):
    forgetful = _write(FORGETFUL, ["x is the forgotten words here"])
    other = _write(OTHER, [STILL_HERE])
    _forget(FORGETFUL, STILL_HERE)
    _seam(monkeypatch, FORGETFUL)
    store._all_forgotten_cache.clear()
    judge = view.judge(other)
    assert judge.closed is False and judge.index_closed is False
    assert store.forgotten_incomplete() == frozenset({forgetful})
    # the value forgotten in the broken bucket stays visible here: said, not hidden
    got = recall.query("pangolinsentinel", project_dir=OTHER)
    assert [r["text"] for r in got.rows] == [STILL_HERE]
    assert got.notes == ("forget-incomplete",)
    assert cli.main(["recall", "pangolinsentinel", "--project", OTHER]) == 0
    assert "forget set is incomplete" in capsys.readouterr().out
    # other projects' briefings say it too
    assert view.snapshot(OTHER).notes() == (NOTE,)


def test_recall_rebuilds_once_the_incomplete_bucket_is_proven_again(
        tmp_checkpoint_dir, monkeypatch):
    forgetful = _write(FORGETFUL, ["x is the forgotten words here"])
    _write(OTHER, [STILL_HERE])
    _forget(FORGETFUL, STILL_HERE)
    real = jsonl.read
    _seam(monkeypatch, FORGETFUL)
    store._all_forgotten_cache.clear()
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()
    assert json.loads(meta["incomplete"]) == [forgetful]
    assert STILL_HERE in _texts()
    monkeypatch.setattr(jsonl, "read", real)     # the ledger reads again
    store._all_forgotten_cache.clear()
    recall._forced_at.clear()
    got = recall.query("pangolinsentinel", project_dir=OTHER)
    assert got.rows == []                        # the rebuild dropped the value
    assert _texts() == {"x is the forgotten words here"}


def test_recall_inject_and_action_recall_print_no_incomplete_line(
        tmp_checkpoint_dir, monkeypatch, capsys):
    belief = ("argocd selfHeal reverts any manual kubectl edit to the "
              "gateway deployment in prod")
    store.write_checkpoint("S-old", {
        "session_id": "S-old", "created": "2026-06-20T00:00:00Z",
        "working_context": {"open_questions": [{
            "text": belief, "trust": "verbatim", "quote": belief[:40],
            "importance": 9, "first_seen": "2026-06-20T00:00:00Z"}],
            "recent_decisions": []},
        "epistemic_snapshot": {}}, project_dir=OTHER)
    _write(FORGETFUL, ["words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(
        "argocd selfHeal reverts manual kubectl edit gateway deployment"))
    assert cli.main(["recall-inject", "--project", OTHER,
                     "--session", "S-now"]) == 0
    assert "forget set" not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(
        "kubectl exec -it deploy/gateway -n prod -- sh"))
    assert cli.main(["action-recall", "--project", OTHER,
                     "--session", "S-now"]) == 0
    assert "forget set" not in capsys.readouterr().out


# ---- the audit and the status ----------------------------------------------


def test_the_audit_cannot_prove_when_the_forget_set_is_incomplete(
        tmp_checkpoint_dir, capsys):
    _write(OTHER, ["words"])
    _write(FORGETFUL, ["more words"])
    assert cli.main(["audit", "privacy", "--project", OTHER]) in (0, 3)
    capsys.readouterr()
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    assert cli.main(["audit", "privacy", "--project", OTHER]) == 3
    out = capsys.readouterr().out
    assert "cannot prove: the forget set is incomplete" in out
    assert store.project_slug(FORGETFUL) in out


def test_the_audit_says_no_bucket_in_scope_under_tenant_scope(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _write(OTHER, ["words"])
    _write(FORGETFUL, ["more words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert cli.main(["audit", "privacy", "--project", OTHER]) == 3
    out = capsys.readouterr().out
    assert "no bucket in scope" in out
    assert store.project_slug(FORGETFUL) not in out


def test_status_names_the_in_scope_bucket_and_its_path(tmp_checkpoint_dir,
                                                       capsys):
    _write(OTHER, ["words"])
    _write(FORGETFUL, ["more words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    assert cli.main(["status", "--project", OTHER]) in (0, 1)
    out = capsys.readouterr().out
    assert "forget set incomplete" in out
    assert store.project_slug(FORGETFUL) in out
    assert str(_events(FORGETFUL)) in out
    payload, _rc = cli.status_payload(OTHER)
    assert [e["slug"] for e in payload["ledgers"]["forget_incomplete"]] == [
        store.project_slug(FORGETFUL)]


def test_status_names_no_bucket_under_tenant_scope(tmp_checkpoint_dir, capsys,
                                                   monkeypatch):
    _write(OTHER, ["words"])
    _write(FORGETFUL, ["more words"])
    _events(FORGETFUL).write_bytes(b"<<<<<<< HEAD\n")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    payload, _rc = cli.status_payload(OTHER)
    assert payload["ledgers"]["forget_incomplete"] == []


# ---- the index built before the ledger broke --------------------------------


def test_an_index_built_before_the_ledger_broke_drops_that_buckets_rows(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write(FORGETFUL, ["the walrusharbor deployment decision"])
    recall.rebuild()
    assert _texts() == {"the walrusharbor deployment decision"}
    _seam(monkeypatch, FORGETFUL)               # the file bytes do not change
    view._judge_memo.clear()                    # a fresh process reads again
    got = recall.query("walrusharbor", project_dir=FORGETFUL)
    assert got.rows == []
    assert "closed" in got.notes
    assert _closed_meta() == [slug]             # the forced rebuild closed it


def test_a_skipped_rebuild_says_stale_when_an_incomplete_bucket_clears(
        tmp_checkpoint_dir, monkeypatch):
    forgetful = _write(FORGETFUL, ["x is the forgotten words here"])
    _write(OTHER, [STILL_HERE])
    real = jsonl.read
    _seam(monkeypatch, FORGETFUL)
    store._all_forgotten_cache.clear()
    recall.rebuild()
    monkeypatch.setattr(jsonl, "read", real)
    store._all_forgotten_cache.clear()
    # a rebuild happened a moment ago, so this query may not force another
    recall._forced_at[str(config.recall_db())] = recall._monotonic()
    got = recall.query("pangolinsentinel", project_dir=OTHER)
    assert "stale" in got.notes
    assert forgetful not in store.forgotten_incomplete()


def test_an_empty_listing_still_carries_the_incomplete_note(
        tmp_checkpoint_dir, capsys):
    # a bucket with a ledger but no checkpoint is not listed, and still
    # makes the forget set incomplete
    bucket = config.checkpoint_dir() / store.project_slug(FORGETFUL)
    bucket.mkdir(parents=True)
    (bucket / "events.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert cli.main(["projects", "--project", OTHER]) == 0
    out = capsys.readouterr().out
    assert "no project buckets yet" in out
    assert NOTE in out
