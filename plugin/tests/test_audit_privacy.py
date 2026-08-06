"""`daimon audit privacy` — read-only tombstone residue audit.

The audit proves forget's contract instead of trusting it: for every
tombstoned content key, no plaintext copy may survive on any surface —
including the `quote`/`scene` fields forget itself does not yet reach,
and files forget's walk does not recognise.
"""
from daimon_briefing import cli, normalize, privacy, store


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
