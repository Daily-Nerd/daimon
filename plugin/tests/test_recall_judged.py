"""The recall index is built and queried through `view.judge` (#1132 PR 9a).

Invariant under test: the index never holds a withheld row, and if a query
finds one anyway the index is rebuilt once. Stores come from the real
writers; the index is read with plain sqlite and as raw bytes, the two ways a
withheld value could still be recovered from it."""

import ast
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from daimon_briefing import (config, display, jsonl, normalize, recall, store,
                             trust)
from daimon_briefing.surfaces import Writer

QUARANTINED = "the quokkasentinel claim was fabricated by the model"
FORGOTTEN = "the pangolinsentinel decision must be erased entirely"
VISIBLE = "a plain decision about the walrusharbor deployment"
HOT = "walrusharbor"


def _cp(sid, decisions, created="2026-08-01T00:00:00Z"):
    return {"session_id": sid, "created": created,
            "working_context": {"recent_decisions": decisions,
                                "open_questions": []},
            "epistemic_snapshot": {}}


def _decision(text):
    return {"text": text, "trust": "inferred"}


def _quarantine(project, text, kind="decision"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


def _forget_value(project, text, item_id="d-gone00"):
    store.append_event(item_id, f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=project, writer=Writer.HUMAN)


def _ids(project):
    got = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    return {d["text"]: d["id"]
            for d in got["working_context"]["recent_decisions"]}


def _rows(db=None):
    conn = sqlite3.connect(str(db or config.recall_db()))
    try:
        return conn.execute("SELECT text, item_id, superseded_by,"
                            " superseded_source FROM items").fetchall()
    finally:
        conn.close()


def _texts():
    return {r[0] for r in _rows()}


def _meta():
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        return dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _fresh_limiter():
    recall._forced_at.clear()
    yield
    recall._forced_at.clear()


def _two_sessions(project="/repo/j"):
    store.write_checkpoint("S1", _cp("S1", [_decision(VISIBLE),
                                            _decision(QUARANTINED),
                                            _decision(FORGOTTEN)]),
                           project_dir=project, writer=Writer.HUMAN)
    return project


# ---- build: classify before insert ---------------------------------------


def test_a_quarantined_row_is_never_inserted(tmp_checkpoint_dir):
    project = _two_sessions()
    _quarantine(project, QUARANTINED)
    recall.rebuild()
    assert _texts() == {VISIBLE, FORGOTTEN}


def test_a_forgotten_value_is_never_inserted(tmp_checkpoint_dir):
    project = _two_sessions()
    _forget_value(project, FORGOTTEN)
    recall.rebuild()
    assert _texts() == {VISIBLE, QUARANTINED}


def test_a_forgotten_id_is_never_inserted_even_with_other_text(
        tmp_checkpoint_dir):
    project = _two_sessions()
    ids = _ids(project)
    _forget_value(project, "wording that no row carries", ids[FORGOTTEN])
    recall.rebuild()
    assert FORGOTTEN not in _texts()
    assert VISIBLE in _texts()


def test_no_withheld_byte_reaches_the_index_file(tmp_checkpoint_dir):
    project = _two_sessions()
    _quarantine(project, QUARANTINED)
    _forget_value(project, FORGOTTEN)
    recall.rebuild()
    blob = config.recall_db().read_bytes()
    assert b"quokkasentinel" not in blob
    assert b"pangolinsentinel" not in blob
    assert b"walrusharbor" in blob


def test_an_old_index_holding_a_quarantined_row_is_replaced(
        tmp_checkpoint_dir):
    """An index the previous schema built (with the quarantined row and its
    free pages) is discarded wholesale on first use: its bytes are gone."""
    project = _two_sessions()
    _quarantine(project, QUARANTINED)
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    recall._init_schema(conn)
    conn.execute("INSERT INTO items (text, kind, project_slug, session_id,"
                 " created) VALUES (?, 'decision', ?, 'S1', 1.0)",
                 (QUARANTINED, store.project_slug(project)))
    conn.execute("INSERT INTO items_fts(rowid, text, quote, scene)"
                 " VALUES (1, ?, '', '')", (QUARANTINED,))
    conn.execute("INSERT INTO meta VALUES ('schema_version', '9')")
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)",
                 (recall._fingerprint(),))
    conn.commit()
    conn.close()
    assert b"quokkasentinel" in db.read_bytes()
    hits = recall.search(HOT, project_dir=project)
    assert [h["text"] for h in hits] == [VISIBLE]
    assert b"quokkasentinel" not in db.read_bytes()
    assert _meta()["schema_version"] == recall._SCHEMA_VERSION == "10"


def test_secure_delete_is_on_for_the_build(tmp_checkpoint_dir, monkeypatch):
    class Spy:
        """sqlite3.Connection is a C type; wrap it to record statements."""

        def __init__(self, conn):
            self.conn = conn
            self.sql = []

        def execute(self, sql, *a):
            self.sql.append(sql)
            return self.conn.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    built = []
    real = sqlite3.connect

    def spy(*a, **k):
        built.append(Spy(real(*a, **k)))
        return built[-1]

    monkeypatch.setattr(recall.sqlite3, "connect", spy)
    _two_sessions()
    recall.rebuild()
    assert "PRAGMA secure_delete=ON" in built[0].sql


def test_judges_are_read_once_per_bucket_per_build(tmp_checkpoint_dir,
                                                   monkeypatch):
    project = _two_sessions()
    _quarantine(project, QUARANTINED)
    names = []
    real = jsonl.read

    def counting(path, *a, **k):
        names.append(path.name)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", counting)
    recall.rebuild()
    assert names.count("trust.jsonl") == 1


# ---- closed buckets -------------------------------------------------------


def _break_trust(project):
    ledger = config.checkpoint_dir() / store.project_slug(project) / "trust.jsonl"
    saved = ledger.read_bytes() if ledger.exists() else b""
    ledger.write_bytes(saved + b"not json at all\n")
    return ledger, saved


def test_a_closed_bucket_has_no_rows_and_is_recorded(tmp_checkpoint_dir):
    project = _two_sessions()
    other = "/repo/other"
    store.write_checkpoint("S2", _cp("S2", [_decision("an unrelated "
                                                      "walrusharbor note")]),
                           project_dir=other, writer=Writer.HUMAN)
    _break_trust(project)
    recall.rebuild()
    assert _texts() == {"an unrelated walrusharbor note"}
    assert json.loads(_meta()["closed"]) == [store.project_slug(project)]


def test_a_query_touching_a_closed_bucket_says_so(tmp_checkpoint_dir):
    project = _two_sessions()
    _break_trust(project)
    got = recall.query(HOT, project_dir=project)
    assert got.rows == []
    assert "closed" in got.notes
    elsewhere = recall.query(HOT, project_dir="/repo/not-closed")
    assert "closed" not in elsewhere.notes


def test_a_reopened_bucket_forces_a_rebuild_on_query(tmp_checkpoint_dir,
                                                     monkeypatch):
    project = _two_sessions()
    ledger, saved = _break_trust(project)
    assert recall.query(HOT, project_dir=project).rows == []
    ledger.write_bytes(saved)
    # the ledger change is a fingerprint input, so the index also refreshes
    # on its own; pin the forced path by hiding the fingerprint refresh
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    recall._forced_at.clear()
    got = recall.query(HOT, project_dir=project)
    assert [r["text"] for r in got.rows][:1] == [VISIBLE]
    assert "closed" not in got.notes


def test_the_forced_rebuild_is_rate_limited(tmp_checkpoint_dir, monkeypatch):
    project = _two_sessions()
    clock = [100.0]
    monkeypatch.setattr(recall, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    recall.rebuild()
    builds = []
    real = recall.rebuild

    def counting():
        builds.append(1)
        return real()

    monkeypatch.setattr(recall, "rebuild", counting)
    _quarantine(project, QUARANTINED)
    got = recall.query("quokkasentinel", project_dir=project)
    assert got.rows == [] and len(builds) == 1
    assert QUARANTINED not in _texts()
    # a second drop inside the window is dropped without another rebuild
    _forget_value(project, FORGOTTEN)
    clock[0] += 5
    got = recall.query("pangolinsentinel", project_dir=project)
    assert got.rows == [] and len(builds) == 1
    clock[0] += 31
    recall.query("pangolinsentinel", project_dir=project)
    assert len(builds) == 2


# ---- query: judged rows ---------------------------------------------------


def test_search_keeps_its_public_shape(tmp_checkpoint_dir):
    project = _two_sessions()
    rows = recall.search(HOT, project_dir=project)
    assert isinstance(rows, list) and rows
    assert all(isinstance(r, dict) for r in rows)
    assert "scene" not in rows[0] and "_scene" not in rows[0]
    assert {"text", "quote", "trust", "kind", "author", "project_slug",
            "session_id", "created", "superseded_by", "item_id",
            "match_score", "rank_score"} <= set(rows[0])
    assert recall.query(HOT, project_dir=project).rows == rows


def test_a_stale_index_row_is_dropped_and_the_index_rebuilt_once(
        tmp_checkpoint_dir, monkeypatch):
    project = _two_sessions()
    recall.rebuild()
    assert QUARANTINED in _texts()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    _quarantine(project, QUARANTINED)
    builds = []
    real = recall.rebuild
    monkeypatch.setattr(recall, "rebuild",
                        lambda: builds.append(1) or real())
    got = recall.query("quokkasentinel", project_dir=project)
    assert got.rows == []
    assert builds == [1]
    assert QUARANTINED not in _texts()
    assert b"quokkasentinel" not in config.recall_db().read_bytes()


def test_a_withheld_row_never_fills_a_slot(tmp_checkpoint_dir, monkeypatch):
    project = "/repo/slots"
    store.write_checkpoint("S1", _cp("S1", [
        _decision(QUARANTINED + " walrusharbor"),
        _decision(VISIBLE)]), project_dir=project, writer=Writer.HUMAN)
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    _quarantine(project, QUARANTINED + " walrusharbor")
    got = recall.query(HOT, project_dir=project, limit=1)
    assert [r["text"] for r in got.rows] == [VISIBLE]


def test_a_stale_note_when_the_refresh_fails(tmp_checkpoint_dir, monkeypatch):
    project = _two_sessions()
    recall.rebuild()

    def boom():
        raise OSError("disk")

    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    got = recall.query(HOT, project_dir=project)
    assert got.rows and "stale" in got.notes


def test_a_failed_refresh_still_serves_through_the_judge(tmp_checkpoint_dir,
                                                         monkeypatch):
    project = _two_sessions()
    recall.rebuild()
    _quarantine(project, QUARANTINED)

    def boom():
        raise OSError("disk")

    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    monkeypatch.setattr(recall, "rebuild", boom)
    got = recall.query("quokkasentinel", project_dir=project)
    assert got.rows == []
    assert "stale" in got.notes


# ---- find / lookup_item ---------------------------------------------------


def test_find_returns_found_withheld_or_absent(tmp_checkpoint_dir,
                                               monkeypatch):
    project = _two_sessions()
    ids = _ids(project)
    slug = store.project_slug(project)
    got = recall.find(ids[VISIBLE], slug=slug)
    assert isinstance(got, recall.Found) and got.row["text"] == VISIBLE
    assert isinstance(recall.find("d-000000", slug=slug), recall.Absent)
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    _quarantine(project, QUARANTINED)
    verdict = recall.find(ids[QUARANTINED], slug=slug)
    assert isinstance(verdict, recall.Withheld)
    assert verdict.reason == "quarantine"
    assert QUARANTINED not in repr(verdict)
    assert QUARANTINED not in _texts()


def test_lookup_item_keeps_its_shape(tmp_checkpoint_dir):
    project = _two_sessions()
    ids = _ids(project)
    slug = store.project_slug(project)
    row = recall.lookup_item(ids[VISIBLE], slug=slug)
    assert isinstance(row, dict) and row["text"] == VISIBLE
    assert recall.lookup_item("d-000000", slug=slug) is None
    _quarantine(project, QUARANTINED)
    assert recall.lookup_item(ids[QUARANTINED], slug=slug) is None


# ---- suggest --------------------------------------------------------------


def test_withheld_terms_do_not_count_toward_the_overlap(tmp_checkpoint_dir,
                                                        monkeypatch):
    """One visible item shares ONE term with the prompt; a second item of the
    same session shares the other but is quarantined. Together they would
    clear _MIN_OVERLAP; judged first, the session stays silent."""
    project = "/repo/overlap"
    store.write_checkpoint("S1", _cp("S1", [
        _decision("kestrelmarker rollout notes for the team"),
        _decision("falconmarker rollout notes for the quarantine")]),
        project_dir=project, writer=Writer.HUMAN)
    prompt = "check kestrelmarker and falconmarker status"
    assert recall.suggest(prompt, project_dir=project)
    _quarantine(project, "falconmarker rollout notes for the quarantine")
    assert recall.suggest(prompt, project_dir=project) == []


def test_a_clean_suggest_reads_the_trust_ledger_once(tmp_checkpoint_dir,
                                                     monkeypatch):
    project = _two_sessions()
    _quarantine(project, "an unrelated claim that is fabricated")
    names = []
    real = jsonl.read

    def counting(path, *a, **k):
        names.append(path.name)
        return real(path, *a, **k)

    monkeypatch.setattr(jsonl, "read", counting)
    recall.suggest("walrusharbor deployment decision", project_dir=project)
    assert names.count("trust.jsonl") == 1
    recall.suggest("walrusharbor deployment decision", project_dir=project)
    assert names.count("trust.jsonl") == 1


def test_suggest_keeps_its_public_shape(tmp_checkpoint_dir):
    project = _two_sessions()
    out = recall.suggest("walrusharbor deployment decision",
                         project_dir=project)
    assert isinstance(out, list)
    assert all(isinstance(r, dict) and "scene" not in r and "_scene" not in r
               for r in out)


# ---- supersession over all rows -------------------------------------------


def _supersession_world(project, *, owner_text, owner_trust="inferred"):
    store.write_checkpoint("S-old", _cp("S-old", [
        _decision("migrate the walrus ledger storage from postgres to sqlite")
    ], created="2026-07-01T00:00:00Z"), project_dir=project, writer=Writer.HUMAN)
    cp = _cp("S-new", [{
        "text": owner_text, "trust": owner_trust,
        "links": [{"type": "supersedes",
                   "target": "migrate the walrus ledger storage"
                             " from postgres to sqlite"}]}],
        created="2026-08-01T00:00:00Z")
    store.write_checkpoint("S-new", cp, project_dir=project, writer=Writer.HUMAN)


OWNER = "we moved the walrus ledger storage to the dolphin cluster instead"


def test_a_visible_superseder_names_its_session(tmp_checkpoint_dir):
    _supersession_world("/repo/sup", owner_text=OWNER)
    recall.rebuild()
    marks = {r[0]: r[2:] for r in _rows()}
    old = "migrate the walrus ledger storage from postgres to sqlite"
    assert marks[old] == ("S-new", "link")


def test_a_withheld_superseder_marks_with_the_generic_label(
        tmp_checkpoint_dir):
    _supersession_world("/repo/sup2", owner_text=OWNER)
    _quarantine("/repo/sup2", OWNER)
    recall.rebuild()
    rows = {r[0]: r[2:] for r in _rows()}
    assert OWNER not in rows
    old = "migrate the walrus ledger storage from postgres to sqlite"
    assert rows[old] == ("resolved", "link")
    assert b"S-new" not in config.recall_db().read_bytes()


def test_a_withheld_candidate_keeps_a_free_text_target_ambiguous(
        tmp_checkpoint_dir):
    """Two earlier items match the target text; one is withheld. The match is
    still ambiguous (never guess), so the visible one is NOT marked."""
    project = "/repo/amb"
    store.write_checkpoint("S-a", _cp("S-a", [
        _decision("migrate the walrus ledger storage from postgres to sqlite")
    ], created="2026-06-01T00:00:00Z"), project_dir=project, writer=Writer.HUMAN)
    twin = "walrus ledger storage migration from postgres to sqlite planned"
    store.write_checkpoint("S-b", _cp("S-b", [_decision(twin)],
                                      created="2026-07-01T00:00:00Z"),
                           project_dir=project, writer=Writer.HUMAN)
    cp = _cp("S-new", [{"text": OWNER, "trust": "inferred", "links": [
        {"type": "supersedes",
         "target": "migrate walrus ledger storage postgres sqlite"}]}],
        created="2026-08-01T00:00:00Z")
    store.write_checkpoint("S-new", cp, project_dir=project, writer=Writer.HUMAN)
    _quarantine(project, twin)
    recall.rebuild()
    rows = {r[0]: r[2:] for r in _rows()}
    assert twin not in rows
    assert rows["migrate the walrus ledger storage from postgres to sqlite"
                ] == (None, None)


def test_a_resolution_naming_a_withheld_id_is_generic(tmp_checkpoint_dir):
    project = "/repo/res"
    store.write_checkpoint("S1", _cp("S1", [_decision(VISIBLE),
                                            _decision(QUARANTINED)]),
                           project_dir=project, writer=Writer.HUMAN)
    ids = _ids(project)
    store.append_event(ids[VISIBLE], f"superseded-by:{ids[QUARANTINED]}",
                       project_dir=project, writer=Writer.HUMAN)
    recall.rebuild()
    assert {r[0]: r[2:] for r in _rows()}[VISIBLE] == (
        ids[QUARANTINED], "resolution")
    _quarantine(project, QUARANTINED)
    recall.rebuild()
    assert {r[0]: r[2:] for r in _rows()}[VISIBLE] == (
        "resolved", "resolution")
    assert ids[QUARANTINED].encode() not in config.recall_db().read_bytes()


# ---- single flight --------------------------------------------------------


def test_two_threads_on_a_stale_index_rebuild_once(tmp_checkpoint_dir,
                                                   monkeypatch):
    project = _two_sessions()
    _quarantine(project, QUARANTINED)
    builds = []
    real = recall._rebuild_locked
    gate = threading.Barrier(2)

    def slow(path):
        builds.append(1)
        return real(path)

    monkeypatch.setattr(recall, "_rebuild_locked", slow)
    results = []

    def worker():
        gate.wait()
        results.append(recall.query(HOT, project_dir=project).rows)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1
    assert len(results) == 2
    for rows in results:
        assert QUARANTINED not in [r["text"] for r in rows]
    assert QUARANTINED not in _texts()


def test_the_tmp_file_is_private_and_uniquely_named(tmp_checkpoint_dir,
                                                    monkeypatch):
    _two_sessions()
    seen = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        seen.append(Path(str(target)))
        return real(target, *a, **k)

    monkeypatch.setattr(recall.sqlite3, "connect", spy)
    modes = []
    real_replace = recall.os.replace

    def replace(src, dst):
        modes.append(Path(src).stat().st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(recall.os, "replace", replace)
    recall.rebuild()
    tmp = seen[0]
    db = config.recall_db()
    assert tmp.name.startswith(f"{db.name}.{recall.os.getpid()}.tmp.")
    assert modes == [0o600]
    assert not tmp.exists()


def test_nothing_reachable_from_rebuild_refreshes_the_index():
    """`_ensure_fresh` takes the per-path lock and calls rebuild; a rebuild
    that called it back (or `warm`) would deadlock on a lock it holds."""
    path = Path(recall.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def calls(fn):
        return {c.func.id for c in ast.walk(fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                } | {c.func.attr for c in ast.walk(fn)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Attribute)}

    reach, todo = set(), ["rebuild"]
    while todo:
        name = todo.pop()
        if name in reach or name not in funcs:
            continue
        reach.add(name)
        todo.extend(calls(funcs[name]) & set(funcs))
    assert {"rebuild", "_rebuild_locked"} <= reach
    assert not reach & {"_ensure_fresh", "warm"}, reach


# ---- the note presenter ---------------------------------------------------


def test_recall_note_is_one_line_with_no_counts():
    assert display.recall_note(()) is None
    stale = display.recall_note(("stale",))
    closed = display.recall_note(("closed",))
    both = display.recall_note(("stale", "closed"))
    for line in (stale, closed, both):
        assert line and "\n" not in line
        assert not any(ch.isdigit() for ch in line)
    assert stale != closed
    assert "index" in stale and "trust" in closed


# ---- paths the happy tests do not reach ----------------------------------


def test_a_kind_the_table_does_not_know_is_judged_by_value(tmp_checkpoint_dir):
    field = recall._field_for("not-a-kind")
    assert field.kind == "not-a-kind" and field.section == ""


def test_a_corrupt_read_rebuilds_once_and_retries(tmp_checkpoint_dir,
                                                  monkeypatch):
    project = _two_sessions()
    recall.rebuild()
    real = recall._select
    calls = []

    def flaky(sql, params):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.DatabaseError("malformed")
        return real(sql, params)

    monkeypatch.setattr(recall, "_select", flaky)
    got = recall.query(HOT, project_dir=project)
    assert [r["text"] for r in got.rows][:1] == [VISIBLE]


def test_a_corrupt_read_that_stays_corrupt_gives_up_empty(tmp_checkpoint_dir,
                                                          monkeypatch):
    project = _two_sessions()
    recall.rebuild()

    def broken(sql, params):
        raise sqlite3.DatabaseError("malformed")

    monkeypatch.setattr(recall, "_select", broken)
    got = recall.query(HOT, project_dir=project)
    assert got.rows == []


def test_suggest_rebuilds_once_when_the_judge_drops_a_row(tmp_checkpoint_dir,
                                                          monkeypatch):
    project = "/repo/sg"
    store.write_checkpoint("S1", _cp("S1", [
        _decision("kestrelmarker rollout notes for the team"),
        _decision("falconmarker rollout notes for the team")]),
        project_dir=project, writer=Writer.HUMAN)
    prompt = "check kestrelmarker and falconmarker status"
    recall.rebuild()
    monkeypatch.setattr(recall, "_ensure_fresh", lambda: None)
    _quarantine(project, "falconmarker rollout notes for the team")
    builds = []
    real = recall.rebuild
    monkeypatch.setattr(recall, "rebuild", lambda: builds.append(1) or real())
    assert recall.suggest(prompt, project_dir=project) == []
    assert builds == [1]
    assert "falconmarker rollout notes for the team" not in _texts()


def test_a_fold_that_raises_closes_the_judge_and_is_not_memoized(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import view
    slug = store.project_slug(_two_sessions())

    def boom(*_a, **_k):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(view, "forgotten_ids", boom)
    first = view.judge(slug)
    assert first.closed and not first.empty
    assert view.judge(slug) is not first
    monkeypatch.undo()
    monkeypatch.setattr(view, "forgotten_keys", boom)
    assert view.judge(slug).closed
    assert view.judge(None).closed
