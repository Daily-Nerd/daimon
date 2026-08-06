"""`daimon audit privacy` — read-only tombstone residue audit.

The audit proves forget's contract instead of trusting it: for every
tombstoned content key, no plaintext copy may survive on any surface —
including the `quote`/`scene` fields forget itself does not yet reach,
and files forget's walk does not recognise.
"""
import json
import sqlite3

import pytest

from daimon_briefing import cli, config, normalize, privacy, render, store


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
    hits = [f for f in result["findings"] if f["content_hash"] == key]
    assert hits
    # Pin the SURFACE, not just the hash: the rotated pointer is the #583 hole
    # (forget reasoned about the live checkpoint only), so a finding that came
    # from anywhere else would silently pass this test.
    assert any(f["path"].endswith("prev-1.json") for f in hits), \
        f"expected a prev-1.json finding, got {[f['path'] for f in hits]}"


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


def test_residue_in_active_topic_detected(tmp_checkpoint_dir):
    """#599 class finding: active_topic is a singleton outside _ITEM_LISTS —
    indexed for retrieval (schema.KIND_SOURCES) but invisible to an auditor
    iterating the list sections only. An audit blind to it certifies exit 0
    over live plaintext."""
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": KEEPER, "quote": CANARY,
                             "trust": "verbatim"},
            "recent_decisions": [{"text": KEEPER, "trust": "inferred"}]},
    }, project_dir=PROJECT)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["content_hash"] == key for f in result["findings"]), \
        "active_topic residue must be detected"


def test_residue_in_link_target_detected(tmp_checkpoint_dir):
    """#599 class finding: links[].target copies another item's whole text
    (the serializer's supersedes contract) and redact_checkpoint scrubs it —
    so it is a plaintext carrier the audit must hash too."""
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": KEEPER, "trust": "inferred",
             "links": [{"type": "supersedes", "target": CANARY}]}]},
    }, project_dir=PROJECT)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["content_hash"] == key for f in result["findings"]), \
        "links[].target residue must be detected"


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


def test_residue_in_orphan_tmp_detected(tmp_checkpoint_dir):
    from daimon_briefing import recall
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    db = _make_recall_db(tmp_checkpoint_dir, [(CANARY, slug)],
                         recall._fingerprint())
    orphan = db.with_name("recall.db.99999.tmp")
    db.rename(orphan)          # the crashed-write shape: full snapshot, tmp name
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "orphan-tmp" and f["content_hash"] == key
               for f in result["findings"])


def test_corrupt_orphan_is_unscannable_not_crash(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    orphan = config.recall_db().with_name("recall.db.11111.tmp")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"not a sqlite file")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(orphan) in result["unscannable"]


def test_residue_in_team_copy_detected(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    team_file = (config.team_dir() / "github-com-example-memories" / "projects"
                 / "x" / "y" / "authors" / "someone" / "S9.json")
    team_file.parent.mkdir(parents=True, exist_ok=True)
    team_file.write_text(json.dumps({
        "session_id": "S9", "project_slug": slug,
        "working_context": {"recent_decisions": [
            {"text": CANARY, "id": "i-t", "trust": "inferred"}]},
    }), encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "team-copy" and f["content_hash"] == key
               for f in result["findings"])


def test_team_cross_project_leak_prevented(tmp_checkpoint_dir):
    """Team file under author dir NAMED the project slug, but different payload slug → not flagged."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    other_slug = "different-project-slug"
    # Author dir named SAME as audited project's slug, but payload is different project
    team_file = (config.team_dir() / "github-com-example-memories" / "projects"
                 / "x" / "y" / "authors" / slug / "S9.json")
    team_file.parent.mkdir(parents=True, exist_ok=True)
    team_file.write_text(json.dumps({
        "session_id": "S9", "project_slug": other_slug,
        "working_context": {"recent_decisions": [
            {"text": CANARY, "id": "i-t", "trust": "inferred"}]},
    }), encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    # Must NOT be in findings (payload slug is different, so team file is not this project's)
    assert not any(f["surface"] == "team-copy" and f["content_hash"] == key
                   for f in result["findings"])


def test_corrupt_team_file_is_unscannable(tmp_checkpoint_dir):
    """Malformed JSON in team dir → path in unscannable, not a crash."""
    _write("S1", KEEPER)
    corrupt_file = config.team_dir() / "github-com-example-memories" / "projects" / "a" / "b"
    corrupt_file.mkdir(parents=True, exist_ok=True)
    (corrupt_file / "corrupt.json").write_text("{this is not valid json", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(corrupt_file / "corrupt.json") in result["unscannable"]


def test_team_findings_dont_count_toward_surfaces_scanned(tmp_checkpoint_dir):
    """Team surfaces don't move surfaces_scanned/zero_surfaces; those are checkpoint-only metrics."""
    # Project with NO local checkpoints, one matching team file
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    team_file = (config.team_dir() / "github-com-example-memories" / "projects"
                 / "x" / "y" / "authors" / "someone" / "S9.json")
    team_file.parent.mkdir(parents=True, exist_ok=True)
    team_file.write_text(json.dumps({
        "session_id": "S9", "project_slug": slug,
        "working_context": {"recent_decisions": [
            {"text": CANARY, "id": "i-t", "trust": "inferred"}]},
    }), encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    # surfaces_scanned should be 0 (no local checkpoints)
    assert result["surfaces_scanned"] == 0
    # zero_surfaces should be True (no local checkpoints)
    assert result["zero_surfaces"] is True
    # But team finding should still be present
    assert any(f["surface"] == "team-copy" and f["content_hash"] == key
               for f in result["findings"])


def test_verbatim_note_detected(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-y", "resolved", note=CANARY, project_dir=PROJECT)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    found = [f for f in result["findings"]
             if f["surface"] == "events-note" and f["content_hash"] == key]
    assert len(found) > 0, "events-note finding not detected"
    assert found[0]["item_id"] == "i-y", "item_id must match event's item_ref"


def test_chunk_cache_reported_at_store_level(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    cache = tmp_checkpoint_dir / ".chunk-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "abc123.json").write_text("{}", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["cache"]["entries"] == 1
    assert result["cache"]["oldest_days"] is not None


def test_tombstone_own_note_detected(tmp_checkpoint_dir):
    """Regression: tombstone's reason field (note) carrying the value must be caught."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    # User pasted the value as --reason when forgetting
    store.append_event("i-x", f"forgotten:{key}", note=CANARY, kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    found = [f for f in result["findings"]
             if f["surface"] == "events-note" and f["content_hash"] == key]
    assert len(found) > 0, "tombstone note containing value must be detected"
    assert found[0]["item_id"] == "i-x", "item_id must match tombstone event's id"


def test_exit_code_semantics():
    clean = {"findings": [], "informational": [], "unscannable": [],
             "surfaces_scanned": 3, "zero_surfaces": False, "cache": {}}
    residue = dict(clean, findings=[{"path": "p", "item_id": None,
                                     "content_hash": "h",
                                     "surface": "checkpoint"}])
    unscan = dict(clean, unscannable=["p"])
    zero = dict(clean, zero_surfaces=True, surfaces_scanned=0)
    stale_only = dict(clean, informational=[{"path": "p", "item_id": None,
                                             "content_hash": "h",
                                             "surface": "stale-index-pending-rebuild"}])
    assert privacy.exit_code([clean]) == 0
    assert privacy.exit_code([residue]) == 1
    assert privacy.exit_code([unscan]) == 3
    assert privacy.exit_code([zero]) == 3
    assert privacy.exit_code([residue, unscan]) == 1   # residue dominates
    assert privacy.exit_code([]) == 3   # empty set = unaudited = cannot-prove
    assert privacy.exit_code([stale_only]) == 0   # informational never affects exit code


def test_audit_all_uses_per_project_tombstones(tmp_checkpoint_dir):
    # CANARY legitimately lives in project B; forgotten only in project A.
    # A per-bucket audit must NOT read that as B's residue (the global union
    # would — that is the false positive the reviewer refuted).
    _write("S1", KEEPER)                       # project A (PROJECT)
    other = "/p/other-project"
    _write("S3", CANARY, project_dir=other)    # project B
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    results = privacy.audit_all()
    b = next(r for r in results
             if r["slug"] == store.project_slug(other))
    assert not any(f["content_hash"] == key for f in b["findings"])


def test_render_never_prints_plaintext(tmp_checkpoint_dir, capsys):
    _write("S1", CANARY)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["findings"], "fixture must produce residue"
    render.render_privacy_audit([result])
    out = capsys.readouterr().out
    assert CANARY not in out
    assert key in out


def _daimon_home_snapshot():
    """(relative_path, bytes) for everything under ~/.daimon except logs/."""
    home = config.checkpoint_dir().parent
    out = {}
    for p in sorted(home.rglob("*")):
        if p.is_file() and "logs" not in p.relative_to(home).parts:
            out[str(p.relative_to(home))] = p.read_bytes()
    return out


def test_cli_audit_privacy_runs_and_exits_by_contract(tmp_checkpoint_dir):
    _write("S1", CANARY)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 1
    # After a REAL forget (which scrubs), audit proves clean.
    _forget(CANARY)
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 0


def test_cli_audit_is_read_only_outside_logs(tmp_checkpoint_dir):
    """Byte-identical AND sidecar-free: every scannable surface shape must be
    on disk before the snapshot, or the assertion has nothing to bite on. A
    `mode=ro` open of a WAL-mode sqlite file still CREATES `-shm`/`-wal`, so
    the orphan snapshots are opened `immutable=1` — asserted here."""
    from daimon_briefing import recall
    _write("S1", CANARY)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    db = _make_recall_db(tmp_checkpoint_dir, [(CANARY, slug)],
                         recall._fingerprint())
    orphan = db.with_name("recall.db.4242.tmp")
    orphan.write_bytes(db.read_bytes())      # crashed-rebuild snapshot
    conn = sqlite3.connect(orphan)           # ...left in WAL mode by the crash
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()                             # clean close removes -wal/-shm
    _team_file(("acme", "backend"), "someone", "S9", slug, CANARY)
    before = _daimon_home_snapshot()
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 1, \
        "fixture must exercise the residue path, not the empty path"
    assert _daimon_home_snapshot() == before
    for sidecar in ("recall.db-wal", "recall.db-shm",
                    "recall.db.4242.tmp-wal", "recall.db.4242.tmp-shm"):
        assert not (db.parent / sidecar).exists(), \
            f"audit created {sidecar} — the read must leave no sqlite sidecar"


# ---- C1: the event ledger carries plaintext in THREE fields, not one ----


def test_event_item_text_carrying_value_detected(tmp_checkpoint_dir):
    """`daimon resolve` / `reopen` pass the item's full text as `item_text`
    (store.append_event) with no forget gate, and events.jsonl is never
    rewritten — so resolve-then-forget leaves the value on disk."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-y", "resolved", item_text=CANARY, project_dir=PROJECT)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    found = [f for f in result["findings"]
             if f["surface"] == "events-note" and f["content_hash"] == key]
    assert found, "item_text residue in the event ledger must be detected"
    assert found[0]["item_id"] == "i-y"


def test_event_status_carrying_value_detected(tmp_checkpoint_dir):
    """`status` is free-form by design (readers prefix-match), so a user's own
    resolution wording can BE the forgotten value."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-y", CANARY, project_dir=PROJECT)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    found = [f for f in result["findings"]
             if f["surface"] == "events-note" and f["content_hash"] == key]
    assert found, "status residue in the event ledger must be detected"
    assert found[0]["item_id"] == "i-y"


def test_tombstone_status_never_self_reports(tmp_checkpoint_dir):
    """The tombstone's own status is `forgotten:<hash>` — hashing that STRING
    can never equal the key it names, so no special case is needed and none
    may be faked into existence either."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["findings"] == []
    assert privacy.exit_code([result]) == 0


def test_non_utf8_events_ledger_is_unscannable(tmp_checkpoint_dir):
    """A non-UTF-8 ledger raises UnicodeDecodeError (a ValueError) out of
    read_text — cannot-check, never a crash and never silent-clean."""
    _write("S1", KEEPER)
    events = tmp_checkpoint_dir / store.project_slug(PROJECT) / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_bytes(b"\xff\xfe garbage")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(events) in result["unscannable"]
    assert privacy.exit_code([result]) == 3


def test_torn_and_non_dict_event_lines_are_skipped(tmp_checkpoint_dir):
    """The ledger is append-only and can be torn mid-write. A junk line is a
    line, not a reason to stop reading the rest of the file."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    events = tmp_checkpoint_dir / store.project_slug(PROJECT) / "events.jsonl"
    with events.open("a", encoding="utf-8") as f:
        f.write('{"kind": "resolution", "item_ref": "i-t\n')   # torn
        f.write('"a bare string, not a row"\n')                # non-dict
    store.append_event("i-y", "resolved", note=CANARY, project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "events-note" and f["item_id"] == "i-y"
               for f in result["findings"])
    assert str(events) not in result["unscannable"]


def test_non_dict_item_in_a_checkpoint_list_is_skipped(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    odd = tmp_checkpoint_dir / store.project_slug(PROJECT) / "S9.json"
    odd.write_text(json.dumps({
        "session_id": "S9", "project_slug": store.project_slug(PROJECT),
        "working_context": {"recent_decisions": ["not an item", None]},
    }), encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(odd) not in result["unscannable"]
    assert result["findings"] == []


def test_team_segments_only_reads_the_nested_layout(tmp_path):
    root = tmp_path / "team"
    nested = root / "remote" / "projects" / "acme" / "backend" / "authors" / "me" / "S1.json"
    assert privacy._team_segments(root, nested) == ("acme", "backend")
    flat = root / "remote" / "authors" / "me" / "S1.json"
    assert privacy._team_segments(root, flat) is None
    assert privacy._team_segments(root, tmp_path / "elsewhere" / "S1.json") is None


def test_unreadable_index_location_is_unscannable(tmp_path):
    """`Path.exists()` raises (not returns False) when the parent dir denies
    access — a stat we cannot do is cannot-prove, never vacuously clean."""
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "recall.db").write_bytes(b"x")
    locked.chmod(0o000)
    try:
        _res, _info, readable = privacy._scan_recall_db(
            locked / "recall.db", "slug", set())
    finally:
        locked.chmod(0o755)
    assert readable is None


def test_walks_that_raise_degrade_instead_of_crashing(
        tmp_checkpoint_dir, monkeypatch):
    """The orphan glob and the team rglob are best-effort. A filesystem that
    raises mid-walk must degrade to "nothing found there", never abort the
    audit — and pathlib's globs swallow EACCES, so the error is injected."""
    _write("S1", KEEPER)

    def boom(*_a, **_kw):
        raise OSError("walk exploded")

    monkeypatch.setattr(privacy.Path, "glob", boom)
    monkeypatch.setattr(privacy.Path, "rglob", boom)
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["findings"] == []
    assert result["surfaces_scanned"] > 0


def test_unreadable_bucket_dir_is_unscannable(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    bucket = tmp_checkpoint_dir / store.project_slug(PROJECT)
    bucket.chmod(0o000)
    try:
        result = privacy.audit_project(project_dir=PROJECT)
    finally:
        bucket.chmod(0o755)
    assert str(bucket) in result["unscannable"]
    assert privacy.exit_code([result]) == 3


# ---- C2: known plaintext-free sidecars are not "unknown" ----


def test_known_sidecar_files_are_not_unscannable(tmp_checkpoint_dir):
    """Every one of these is plaintext-free BY CONSTRUCTION (receipts hold
    hashes+JWS, verification.jsonl a pointer+reason code, forget-hits.jsonl
    {ts,key}, the lock file is empty, .DS_Store is Finder metadata). Reporting
    them made exit 0 unreachable on a real install."""
    _write("S1", KEEPER)
    bucket = tmp_checkpoint_dir / store.project_slug(PROJECT)
    for d in (bucket, tmp_checkpoint_dir):
        (d / "S1.receipt").write_text("{}", encoding="utf-8")
        (d / "verification.jsonl").write_text('{"item_ref": "i-1"}\n',
                                              encoding="utf-8")
        (d / "forget-hits.jsonl").write_text('{"key": "h"}\n', encoding="utf-8")
        (d / ".DS_Store").write_bytes(b"\x00\x01")
        (d / store._LOCK_NAME).write_text("", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["unscannable"] == []
    assert privacy.exit_code([result]) == 0


# ---- I1: an unknown file belongs to ITS bucket's audit ----


def test_unknown_file_in_another_bucket_does_not_taint_this_project(
        tmp_checkpoint_dir):
    _write("S1", KEEPER)
    other = "/p/other-project"
    _write("S2", KEEPER, project_dir=other)
    bak = tmp_checkpoint_dir / store.project_slug(other) / "latest.json.bak-9"
    bak.write_text("{}", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(bak) not in result["unscannable"]
    assert privacy.exit_code([result]) == 0
    # ...but it IS that project's problem.
    other_result = privacy.audit_project(project_dir=other)
    assert str(bak) in other_result["unscannable"]


def test_unknown_file_in_flat_dir_taints_every_project(tmp_checkpoint_dir):
    """The flat dir is shared — `latest.json` there may hold ANY project's
    session, so an unrecognised file beside it is every audit's blind spot."""
    _write("S1", KEEPER)
    stray = tmp_checkpoint_dir / "latest.json.bak-7"
    stray.write_text("{}", encoding="utf-8")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(stray) in result["unscannable"]


def test_nested_dir_inside_bucket_is_unscannable(tmp_checkpoint_dir):
    """A directory the walk does not descend into is exactly the `.bak` class:
    unknown shape, unread contents. Report it, never skip it."""
    _write("S1", KEEPER)
    nested = tmp_checkpoint_dir / store.project_slug(PROJECT) / "archive"
    nested.mkdir(parents=True, exist_ok=True)
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(nested) in result["unscannable"]


# ---- I2: a teammate's copy is stamped with the WRITER's slug ----


def _team_file(segs, author, sid, payload_slug, text, remote="github-com-x"):
    path = config.team_dir().joinpath(
        remote, "projects", *segs, "authors", author, f"{sid}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "session_id": sid, "project_slug": payload_slug,
        "working_context": {"recent_decisions": [
            {"text": text, "id": "i-t", "trust": "inferred"}]},
    }), encoding="utf-8")
    return path


def test_teammate_copy_under_shared_project_path_detected(tmp_checkpoint_dir):
    """The spec's motivating case. A teammate's mirror of THIS project stamps
    the writer's own path-derived slug, so payload-slug membership skipped it
    entirely. The `projects/<segs>/` subtree is the project identity (the same
    thing store.read_team filters on) — and this machine's own dual-written
    copy in that subtree is what proves the subtree is ours."""
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    slug = store.project_slug(PROJECT)
    segs = ("acme", "backend")
    mine = store.project_slug(config.author()) or "me"
    _team_file(segs, mine, "S1", slug, KEEPER)         # my own mirror
    theirs = _team_file(segs, "other-author", "S9",
                        "-home-them-checkouts-backend", CANARY)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "team-copy" and f["content_hash"] == key
               and f["path"] == str(theirs) for f in result["findings"])


def test_teammate_copy_under_resolved_team_path_detected(
        tmp_checkpoint_dir, monkeypatch):
    """No local mirror in the subtree at all: the logical path resolves from
    DAIMON_TEAM_PROJECT (teamproject tier 1), exactly as read_team resolves it."""
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "acme/backend")
    _write("S1", KEEPER)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    theirs = _team_file(("acme", "backend"), "other-author", "S9",
                        "-home-them-checkouts-backend", CANARY)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["surface"] == "team-copy" and f["path"] == str(theirs)
               for f in result["findings"])


# ---- I5: best-effort branches must be proven, not assumed ----


def test_residue_in_scene_field_detected(tmp_checkpoint_dir):
    """`scene` is episodic narrative forget does not reach — the audit does."""
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": KEEPER, "scene": CANARY, "trust": "inferred"}]},
    }, project_dir=PROJECT)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    result = privacy.audit_project(project_dir=PROJECT)
    assert any(f["content_hash"] == key and f["surface"] == "checkpoint"
               for f in result["findings"])


def test_non_dict_checkpoint_payload_is_unscannable(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    odd = tmp_checkpoint_dir / store.project_slug(PROJECT) / "S9.json"
    odd.write_text("[1, 2, 3]", encoding="utf-8")
    assert str(odd) in privacy.audit_project(
        project_dir=PROJECT)["unscannable"]


def test_corrupt_local_checkpoint_is_unscannable(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    torn = tmp_checkpoint_dir / store.project_slug(PROJECT) / "S9.json"
    torn.write_text("{this is not valid json", encoding="utf-8")
    assert str(torn) in privacy.audit_project(
        project_dir=PROJECT)["unscannable"]


def test_corrupt_recall_db_is_unscannable(tmp_checkpoint_dir):
    _write("S1", KEEPER)
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"not a sqlite file")
    result = privacy.audit_project(project_dir=PROJECT)
    assert str(db) in result["unscannable"]
    assert privacy.exit_code([result]) == 3


def test_audit_all_with_unreadable_checkpoint_dir_is_cannot_prove(
        tmp_path, monkeypatch):
    dud = tmp_path / "checkpoints-is-a-file"
    dud.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(config, "checkpoint_dir", lambda: dud)
    results = privacy.audit_all()
    assert results == []
    assert privacy.exit_code(results) == 3


def test_audit_without_a_project_is_cannot_prove(tmp_checkpoint_dir):
    """No slug = nothing to scope a scan to. Never "clean"."""
    for no_project in (None, ""):
        result = privacy.audit_project(project_dir=no_project)
        assert result["slug"] is None
        assert result["zero_surfaces"] is True
        assert result["cache"] == {"entries": 0, "oldest_days": None}
        assert privacy.exit_code([result]) == 3


# ---- render: every line shape, hashes only ----


def test_render_covers_every_line_shape(capsys):
    render.render_privacy_audit([{
        "slug": None, "surfaces_scanned": 0, "zero_surfaces": True,
        "findings": [{"path": "/t/S9.json", "item_id": "i-t",
                      "content_hash": "hteam", "surface": "team-copy"}],
        "informational": [{"path": "/t/recall.db", "item_id": "i-r",
                           "content_hash": "hstale",
                           "surface": "stale-index-pending-rebuild"}],
        "unscannable": ["/t/latest.json.bak-1"],
        "cache": {"entries": 2, "oldest_days": 3.0},
    }])
    out = capsys.readouterr().out
    assert "(unknown project)" in out
    assert "WARNING: zero surfaces" in out
    assert "RESIDUE [team-copy] hash hteam" in out
    assert "may also exist upstream" in out
    assert "stale [stale-index-pending-rebuild] hash hstale" in out
    assert "UNSCANNABLE /t/latest.json.bak-1" in out
    assert "chunk cache: 2 entr(y/ies), oldest 3.0d" in out


def test_render_cache_age_unknown_line(capsys):
    render.render_privacy_audit([{
        "slug": "p", "surfaces_scanned": 1, "zero_surfaces": False,
        "findings": [], "informational": [], "unscannable": [],
        "cache": {"entries": 4, "oldest_days": None},
    }])
    out = capsys.readouterr().out
    assert "chunk cache: 4 entr(y/ies) — age unknown" in out
    assert "clean — no tombstoned value found" in out


# ---- CLI surface ----


def test_cli_all_and_project_are_mutually_exclusive(tmp_checkpoint_dir):
    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", "privacy", "--all", "--project", PROJECT])
    assert exc.value.code == 2


def test_usage_tag_distinguishes_the_three_outcomes(tmp_checkpoint_dir):
    """`daimon stats` must be able to tell "ran the auditor" from "the auditor
    found residue" from "the auditor could not prove anything"."""
    usage = config.log_dir() / "usage.log"
    _write("S1", CANARY)
    key = normalize.content_key(CANARY)
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT)
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 1
    assert usage.read_text(encoding="utf-8").rstrip().endswith(
        "audit-privacy:residue")
    assert cli.main(["audit", "privacy", "--project", "/p/never-existed"]) == 3
    assert usage.read_text(encoding="utf-8").rstrip().endswith(
        "audit-privacy:unproven")
    _forget(CANARY)
    assert cli.main(["audit", "privacy", "--project", PROJECT]) == 0
    assert usage.read_text(encoding="utf-8").rstrip().endswith("audit-privacy")


def test_audit_quotes_moved_and_alias_deprecated(tmp_checkpoint_dir, capsys):
    _write("S1", KEEPER)
    assert cli.main(["audit", "quotes", "--project", PROJECT]) == 0
    capsys.readouterr()
    assert cli.main(["audit-quotes", "--project", PROJECT]) == 0
    assert "deprecated" in capsys.readouterr().out.lower()


def test_cli_audit_all_flag(tmp_checkpoint_dir):
    # One bucket, one clean checkpoint, nothing unknown on disk: the ONLY
    # honest answer is 0. `in (0, 3)` would have passed with the auditor
    # reporting cannot-prove for a tree it fully scanned.
    _write("S1", KEEPER)
    assert cli.main(["audit", "privacy", "--all"]) == 0
