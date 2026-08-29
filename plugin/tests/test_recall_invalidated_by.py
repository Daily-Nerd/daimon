"""#835: items.invalidated_by is populated from worldcheck contradiction
evidence — the derived-evidence-only write path.

The slot records that a claim was DISPROVEN against repo reality, which is a
different fact from superseded_by's "replaced": the axes are independent and
neither write touches the other. Authority precedent, pinned here: only the
verification ledger's worldcheck receipt-contradiction rows write the slot.
Capture-time rejection rows (quote / outcome) describe the capture, not later
disproof, and never write; model-flagged contradictions_flagged entries have
no path to it at all.

Like every recall test: the index is derived, so everything folds in at
rebuild from durable sources (verification.jsonl), and the ledger is
fingerprint INPUT so new evidence rebuilds the index instead of serving
stale rows (#245's lesson, applied to a second ledger).
"""

import json

from daimon_briefing import config, recall, store, worldcheck
from tests.test_recall import _cp


def _item(text, ref):
    return {"text": text, "trust": "inferred", "id": ref}


def _write_ledger(slug, rows):
    path = config.checkpoint_dir() / slug / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


def _receipt_row(ref, *, ts="2026-08-29T10:00:00Z", reason="receipt-invalid"):
    return {"ts": ts, "check": "receipt", "item_ref": ref, "reason": reason}


def test_receipt_contradiction_populates_invalidated_by(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-111aaa")])

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"


def test_axes_are_independent_disproof_never_touches_supersession(
        tmp_checkpoint_dir, monkeypatch):
    """An item can be both replaced and disproven; populating one axis never
    clears or writes the other."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa"),
        _item("an unrelated capybara pagination question", "o-222bbb")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    ev = config.checkpoint_dir() / slug / "events.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(
        '{"ts": "2026-08-29T09:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-111aaa", "status": "resolved", "source": "cli"}\n',
        encoding="utf-8")
    _write_ledger(slug, [_receipt_row("o-111aaa")])

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["superseded_by"] == "resolved"
    assert hits[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"

    other = recall.search("capybara pagination question", project_dir="/repo/x")
    assert other
    assert other[0]["superseded_by"] is None
    assert other[0]["invalidated_by"] is None


def test_capture_rejection_rows_never_write_invalidated_by(
        tmp_checkpoint_dir, monkeypatch):
    """quote/outcome rows (#376) are capture-time verification failures —
    the item downgraded to inferred, not disproven against the world."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [
        {"ts": "2026-08-29T10:00:00Z", "check": "quote",
         "item_ref": "o-111aaa", "reason": "quote-not-in-transcript"},
        {"ts": "2026-08-29T10:00:01Z", "check": "outcome",
         "item_ref": "o-111aaa", "reason": "no-signal-cited"},
    ])

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] is None


def test_contradictions_flagged_never_writes_invalidated_by(
        tmp_checkpoint_dir, monkeypatch):
    """A model-flagged contradiction is a standalone claim with no authority
    to mint a target link — self-assertion is the wedge the trust model
    forbids (#835 authority precedent)."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    cp = _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z")
    cp["epistemic_snapshot"]["contradictions_flagged"] = [
        {"text": "the axolotl exporter claim was verified is wrong",
         "trust": "inferred"}]
    store.write_checkpoint("S-1", cp, project_dir="/repo/x")

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert all(h["invalidated_by"] is None for h in hits)


def test_latest_evidence_wins(tmp_checkpoint_dir, monkeypatch):
    """Rows fold in append order: the most recent probe is the current
    world's answer."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [
        _receipt_row("o-111aaa", ts="2026-08-28T10:00:00Z",
                     reason="receipt-tampered"),
        _receipt_row("o-111aaa", ts="2026-08-29T10:00:00Z",
                     reason="receipt-invalid"),
    ])

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"


def test_malformed_ledger_rows_are_skipped(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    path = config.checkpoint_dir() / slug / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "{not json\n"
        '{"ts": "2026-08-29T10:00:00Z", "check": "receipt"}\n'
        '{"ts": null, "check": "receipt", "item_ref": "o-111aaa",'
        ' "reason": "receipt-invalid"}\n',
        encoding="utf-8")

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] is None


def test_verification_ledger_is_fingerprint_input(
        tmp_checkpoint_dir, monkeypatch):
    """#245's lesson for a second ledger: new contradiction evidence must
    rebuild the index, never serve stale NULL rows until an unrelated
    checkpoint write happens to invalidate the db."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits and hits[0]["invalidated_by"] is None

    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-111aaa")])
    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"


def test_verification_rows_fail_open(tmp_checkpoint_dir):
    """The reader's two guard branches: unknown project and an unreadable
    ledger path both answer [] — a ledger read must never take a rebuild
    down."""
    assert store.verification_rows(project_dir=None) == []
    slug = store.project_slug("/repo/x")
    path = config.checkpoint_dir() / slug / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # a directory where the file should be -> OSError on open
    assert store.verification_rows(project_dir="/repo/x") == []


def test_invalidation_check_names_match_worldchecks_ledger():
    """Divergence guard: recall's filter constant and worldcheck's ledger
    check name are pinned equal rather than imported (recall's import graph
    stays free of worldcheck's probe machinery)."""
    assert recall._INVALIDATION_CHECKS == (worldcheck._LEDGER_CHECK,)
