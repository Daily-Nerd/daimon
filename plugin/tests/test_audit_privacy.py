"""`daimon audit privacy` — read-only tombstone residue audit.

The audit proves forget's contract instead of trusting it: for every
tombstoned content key, no plaintext copy may survive on any surface —
including the `quote`/`scene` fields forget itself does not yet reach,
and files forget's walk does not recognise.
"""
import sqlite3
from daimon_briefing import cli, config, normalize, privacy, store


PROJECT = "/p/audit-privacy"
CANARY = "zqxprivcanary4431 rotate the signing key before the next deploy"
KEEPER = "an unrelated decision that must not be reported"


def _write(session_id, *texts, project_dir=PROJECT):
    store.write_checkpoint(session_id, {
        "session_id": session_id,
        "created": f"2026-08-0{session_id[-1]}T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": t, "trust": "inferred"} for t in texts]},
    }, project_dir=project_dir)


def _forget(value):
    assert cli.main(["forget", value, "--project", PROJECT]) == 0


def test_hashes_covers_text_quote_and_scene():
    item = {"text": "a", "quote": "b", "scene": "c", "id": "i-1"}
    assert privacy._hashes(item) == {
        normalize.content_key("a"),
        normalize.content_key("b"),
        normalize.content_key("c"),
    }


def test_residue_in_prev_n_detected(tmp_checkpoint_dir):
    _write("S1", CANARY, KEEPER)
    _write("S2", KEEPER)          # rotation: S1 state now sits in prev-1
    # Tombstone WITHOUT scrubbing: append the ledger event directly, so the
    # plaintext demonstrably remains and the audit must find it.
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["content_hash"] == key for f in result["findings"])


def test_residue_in_quote_field_detected(tmp_checkpoint_dir):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": KEEPER, "quote": CANARY, "trust": "verbatim"}]},
    }, project_dir=PROJECT)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["content_hash"] == key for f in result["findings"])


def test_clean_tree_reports_nothing(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["findings"] == []
    assert result["unscannable"] == []
    assert result["surfaces_scanned"] > 0


def test_reopened_tombstone_not_flagged(tmp_checkpoint_dir):
    _write("S1", CANARY)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    store.append_event("i-x", "reopened", project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert not any(f["content_hash"] == key for f in result["findings"])


def test_other_projects_residue_not_flagged(tmp_checkpoint_dir):
    other = "/p/other-project"
    _write("S1", CANARY, project_dir=other)
    _write("S2", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)   # tombstoned HERE, lives THERE
    result = privacy.audit_project(project_dir=PROJECT)
    assert not any(f["content_hash"] == key for f in result["findings"])


def test_unknown_file_in_bucket_is_unscannable(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    slug = store.project_slug(PROJECT)
    bak = tmp_checkpoint_dir / slug / "latest.json.bak-123"
    bak.write_text("{}", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(bak) in result["unscannable"]


def test_zero_surfaces_is_not_clean(tmp_checkpoint_dir):
    # No checkpoint ever written for this project — scar 0023 class.
    result = privacy.audit_project(project_dir="/p/never-existed")
    assert result["zero_surfaces"] is True


def _make_recall_db(tmp_path, rows, fingerprint):
    """Build a minimal real recall.db shape at the isolated test location.

    Rows can be 2-tuple (text, slug) or 3-tuple (text, slug, author)."""
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE items(id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
        " quote TEXT, scene TEXT, trust TEXT, kind TEXT, author TEXT,"
        " project_slug TEXT, session_id TEXT, created REAL,"
        " superseded_by TEXT, invalidated_by TEXT, importance INTEGER,"
        " first_seen TEXT, item_id TEXT,"
        " pinned INTEGER NOT NULL DEFAULT 0,"
        " frontier INTEGER NOT NULL DEFAULT 0);")
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
    for row in rows:
        if len(row) == 2:
            text, slug = row
            author = None
        else:
            text, slug, author = row
        conn.execute(
            "INSERT INTO items(text, project_slug, item_id, author) VALUES (?, ?, 'i-r', ?)",
            (text, slug, author))
    conn.commit()
    conn.close()
    return db


def test_recall_residue_with_current_fingerprint_is_a_finding(tmp_checkpoint_dir):
    from daimon_briefing import recall
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, slug)], recall._fingerprint())
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "recall-index-residue"
               and f["content_hash"] == key for f in result["findings"])


def test_recall_residue_with_stale_fingerprint_is_informational(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, slug)], "stale-fp")
    result = privacy.audit_project(project_dir=PROJECT)
    assert not any(f["surface"] == "recall-index-residue"
                   for f in result["findings"])
    assert any(f["surface"] == "stale-index-pending-rebuild"
               and f["content_hash"] == key for f in result["informational"])


def test_null_slug_row_reported_as_unattributed(tmp_checkpoint_dir):
    from daimon_briefing import recall
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, None)], recall._fingerprint())
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "unattributed"
               and f["content_hash"] == key for f in result["findings"])


def test_missing_recall_db_is_not_created_by_audit(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    db = config.recall_db()
    assert not db.exists()
    privacy.audit_project(project_dir=PROJECT)
    assert not db.exists(), "audit must never create the recall db"


def test_foreign_author_row_with_matching_tombstone_is_residue(tmp_checkpoint_dir):
    from daimon_briefing import recall
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    # Foreign author (different from self_author project slug)
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, "other-slug", "foreign@example.com")],
                    recall._fingerprint())
    result = privacy.audit_project(project_dir=PROJECT)
    # Foreign author rows answer to machine-global union, so a global tombstone
    # creates a finding
    assert any(f["surface"] == "recall-index-residue"
               and f["content_hash"] == key for f in result["findings"])


def test_different_local_project_row_not_flagged(tmp_checkpoint_dir):
    from daimon_briefing import recall
    other = "/p/other-project"
    _write("S1", KEEPER)
    _write("S2", KEEPER, project_dir=other)
    key = normalize.content_key(CANARY)
    # Tombstone in THIS project
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    # But the row belongs to the OTHER local project with a local author
    other_slug = store.project_slug(other)
    local_author = config.author()  # local author
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, other_slug, local_author)],
                    recall._fingerprint())
    result = privacy.audit_project(project_dir=PROJECT)
    # Other local project's row should not be flagged by this project's audit
    assert not any(f["content_hash"] == key for f in result["findings"])


def test_null_slug_row_with_stale_fingerprint_is_informational(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    # NULL slug row with stale fingerprint
    _make_recall_db(tmp_checkpoint_dir, [(CANARY, None)], "stale-fp")
    result = privacy.audit_project(project_dir=PROJECT)
    # Should appear in informational, not findings
    assert not any(f["surface"] == "unattributed" and f["content_hash"] == key
                   for f in result["findings"])
    assert any(f["surface"] == "stale-index-pending-rebuild"
               and f["content_hash"] == key for f in result["informational"])
