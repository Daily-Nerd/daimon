"""`index_locate.locate`: the read-only id locator over the recall index
(#1132 PR 11a). The index is built by the real `recall.rebuild`; the locator
must answer from the file as it stands, never rebuild it and never write it."""

import sqlite3

import pytest

from daimon_briefing import config, index_locate, recall, schema, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/locate"
SLUG = store.project_slug(PROJECT)
OTHER = "/p/locate-other"


def _cp(sid, created, *, questions=()):
    return {"session_id": sid, "created": created, "author": "ada",
            "working_context": {
                "active_topic": {"text": "the topic", "trust": "inferred"},
                "open_questions": [{"text": t, "trust": "inferred"}
                                   for t in questions]},
            "epistemic_snapshot": {}}


def _write(sid, created, project=PROJECT, **kw):
    store.write_checkpoint(sid, _cp(sid, created, **kw), project_dir=project,
                           writer=Writer.HUMAN)


def _ids(project=PROJECT):
    cp = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    return {item["text"]: item["id"] for _f, item in schema.iter_items(cp)
            if item.get("id")}


@pytest.fixture
def built(tmp_checkpoint_dir):
    _write("S-1", "2026-08-01T00:00:00Z", questions=["who owns the rollout"])
    _write("S-2", "2026-08-02T00:00:00Z",
           questions=["who owns the rollout", "when does the freeze start"])
    _write("S-9", "2026-08-03T00:00:00Z", project=OTHER,
           questions=["a question of another project"])
    recall.rebuild()
    return config.recall_db()


def test_the_schema_version_is_owned_by_the_leaf_and_recall_uses_it():
    assert recall._SCHEMA_VERSION == index_locate.SCHEMA_VERSION == "10"


def test_locate_returns_the_rows_of_the_slug_newest_first(built):
    ids = _ids()
    got = index_locate.locate(built, SLUG, [ids["who owns the rollout"]])
    rows = got.rows[ids["who owns the rollout"]]
    assert got.notes == ()
    assert [r["session_id"] for r in rows] == ["S-2", "S-1"]
    assert rows[0]["text"] == "who owns the rollout"
    assert rows[0]["kind"] == "question" and rows[0]["project_slug"] == SLUG
    assert set(rows[0]) >= {"item_id", "session_id", "created", "kind",
                            "text", "trust", "quote", "author"}


def test_locate_is_scoped_to_the_slug(built):
    other = _ids(OTHER)["a question of another project"]
    assert index_locate.locate(built, SLUG, [other]).rows == {}
    assert other in index_locate.locate(built, store.project_slug(OTHER),
                                        [other]).rows


def test_an_unknown_id_is_not_a_key(built):
    got = index_locate.locate(built, SLUG, ["o-ffffffffffff"])
    assert got.rows == {} and got.notes == ()


def test_no_ids_opens_nothing(tmp_path):
    got = index_locate.locate(tmp_path / "no-such.db", SLUG, [])
    assert got.rows == {} and got.notes == ()


def test_a_missing_index_is_empty_with_a_note(tmp_path):
    got = index_locate.locate(tmp_path / "no-such.db", SLUG, ["o-aaaaaaaaaaaa"])
    assert got.rows == {} and got.notes == ("index_unavailable",)
    assert not (tmp_path / "no-such.db").exists()      # read-only: never created


def test_a_schema_mismatch_is_empty_with_a_note(built):
    conn = sqlite3.connect(str(built))
    conn.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    ids = _ids()
    got = index_locate.locate(built, SLUG, [ids["who owns the rollout"]])
    assert got.rows == {} and got.notes == ("index_unavailable",)


def test_a_file_that_is_not_a_database_is_empty_with_a_note(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite at all, not even close" * 20)
    got = index_locate.locate(bad, SLUG, ["o-aaaaaaaaaaaa"])
    assert got.rows == {} and got.notes == ("index_unavailable",)


def test_locate_never_rebuilds_and_never_writes(built):
    """A stale fingerprint is not this module's business: the file is read as
    it stands, byte for byte."""
    _write("S-3", "2026-08-04T00:00:00Z", questions=["a newer question"])
    before = built.read_bytes()
    stat = built.stat()
    new_id = _ids()["a newer question"]
    got = index_locate.locate(built, SLUG, [new_id])
    assert got.rows == {}                       # the index is behind, by design
    assert built.read_bytes() == before
    assert built.stat().st_mtime_ns == stat.st_mtime_ns


def test_the_connection_is_a_read_only_uri(built, monkeypatch):
    seen = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        seen.append((target, k))
        return real(target, *a, **k)

    monkeypatch.setattr(index_locate.sqlite3, "connect", spy)
    index_locate.locate(built, SLUG, ["o-aaaaaaaaaaaa"])
    (target, kwargs), = seen
    assert target.startswith("file:") and target.endswith("?mode=ro")
    assert kwargs.get("uri") is True


def test_a_path_with_uri_metacharacters_is_quoted(tmp_path, monkeypatch):
    """`?` and `#` in a directory name would otherwise end the URI path."""
    monkeypatch.setenv("DAIMON_RECALL_DB", str(tmp_path / "we?ird #dir" / "r.db"))
    (tmp_path / "we?ird #dir").mkdir()
    _write("S-1", "2026-08-01T00:00:00Z", questions=["who owns the rollout"])
    recall.rebuild()
    ids = _ids()
    got = index_locate.locate(config.recall_db(), SLUG,
                              [ids["who owns the rollout"]])
    assert got.notes == () and ids["who owns the rollout"] in got.rows


def test_ids_are_looked_up_in_chunks(built, monkeypatch):
    assert index_locate._CHUNK == 500
    monkeypatch.setattr(index_locate, "_CHUNK", 2)
    ids = _ids()
    wanted = [ids["who owns the rollout"], ids["when does the freeze start"],
              "o-111111111111", "o-222222222222", "o-333333333333"]
    got = index_locate.locate(built, SLUG, wanted)
    assert set(got.rows) == {ids["who owns the rollout"],
                             ids["when does the freeze start"]}


def test_duplicate_ids_are_asked_once(built):
    ids = _ids()
    one = ids["who owns the rollout"]
    got = index_locate.locate(built, SLUG, [one, one, one])
    assert len(got.rows[one]) == 2


def test_the_module_imports_nothing_of_the_package():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(index_locate.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a leaf imports no sibling module"
            assert not (node.module or "").startswith("daimon_briefing")
