"""#835: items.invalidated_by is populated from worldcheck contradiction
evidence — the derived-evidence-only write path.

The slot records the LATEST CONTRADICTION EVIDENCE for a claim ("was
contradicted by <evidence> at <ts>") — never a present-tense verdict: no
confirmation rows or cure path exist yet, so a populated value means a probe
once contradicted the claim, not that it is currently false. That is a
different fact from superseded_by's "replaced": the axes are independent and
neither write touches the other. Authority precedent, pinned here: only the
verification ledger's worldcheck receipt-contradiction rows write the slot,
scoped to THIS install's author (machine-local evidence never brands a
teammate's mirrored copy). Capture-time rejection rows (quote / outcome)
describe the capture and never write; model-flagged contradictions_flagged
entries have no path to it at all.

Like every recall test: the index is derived, so everything folds in at
rebuild from durable sources (verification.jsonl), and the ledger is
fingerprint INPUT so new evidence rebuilds the index instead of serving
stale rows (#245's lesson, applied to a second ledger).
"""

import json
import sqlite3

from daimon_briefing import config, recall, store, worldcheck
from tests.test_recall import _cp, _write_team_file


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


def test_axes_are_independent_contradiction_never_touches_supersession(
        tmp_checkpoint_dir, monkeypatch):
    """An item can be both replaced and contradicted; populating one axis
    never clears or writes the other."""
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
    the item downgraded to inferred, not later contradicted by the world."""
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


def test_latest_evidence_wins_by_timestamp_never_line_order(
        tmp_checkpoint_dir, monkeypatch):
    """store.resolutions' documented contract, mirrored (#836 review):
    latest by TS, never line order — the ledger interleaves concurrent
    writers and clock-skewed appends, so a LATER line with an EARLIER stamp
    must not win."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [
        _receipt_row("o-111aaa", ts="2026-08-28T10:00:00Z",
                     reason="receipt-tampered"),
        _receipt_row("o-111aaa", ts="2026-08-29T10:00:00Z",
                     reason="receipt-invalid"),
        # Clock-skewed straggler: appended last, stamped earliest.
        _receipt_row("o-111aaa", ts="2026-08-27T10:00:00Z",
                     reason="receipt-tampered"),
        # Unparseable stamp: never displaces a stamped row (the
        # resolutions-fold posture, mirrored).
        _receipt_row("o-111aaa", ts="not-a-timestamp",
                     reason="receipt-tampered"),
    ])

    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"


def test_machine_local_evidence_never_brands_a_teammates_copy(
        tmp_checkpoint_dir, monkeypatch):
    """#836 review: receipt evidence is about THIS install's checkpoint
    bytes, so the write is author-scoped — a teammate's mirrored row of the
    SAME item id stays unmarked."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    # A FRESH dict for the team copy: write_checkpoint stamps author/created
    # into the dict it is handed, and _write_team_file's setdefault would
    # keep those stamps. No `created` either — a stamped 08-01 date would
    # age out of the #113 team retention window and never index.
    _write_team_file("bea", "S-bea", _cp("S-bea", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")]),
        project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-111aaa")])

    recall.search("axolotl exporter claim", project_dir="/repo/x")  # fresh db
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        by_author = dict(conn.execute(
            "SELECT author, invalidated_by FROM items"
            " WHERE item_id = 'o-111aaa'").fetchall())
    finally:
        conn.close()
    assert by_author["ada"] == "receipt:receipt-invalid@2026-08-29T10:00:00Z"
    assert by_author["bea"] is None


def test_fold_reads_and_binds_the_same_bucket(tmp_checkpoint_dir, monkeypatch):
    """#836 review: bucket names are not slug-idempotent (dots munge to '-'),
    so the fold must read THE bucket directory it binds — never re-derive a
    slug from the name and read a sibling bucket's ledger."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    blob = _cp("S-dot", questions=[
        _item("the quokka dotted bucket claim", "o-333ccc")],
        created="2026-08-01T00:00:00Z")
    blob["author"] = "ada"
    blob["project_slug"] = "repo.x"
    config.checkpoint_dir().mkdir(parents=True, exist_ok=True)
    (config.checkpoint_dir() / "S-dot.json").write_text(
        json.dumps(blob), encoding="utf-8")
    _write_ledger("repo.x", [_receipt_row("o-333ccc")])
    # The sibling the OLD re-slugging read ("repo.x" munges to "repo-x"):
    # carries different evidence, which must NOT land on repo.x's rows.
    _write_ledger("repo-x", [
        _receipt_row("o-333ccc", reason="receipt-tampered")])

    recall.search("quokka dotted bucket claim", all_projects=True)  # fresh db
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = conn.execute(
            "SELECT invalidated_by FROM items"
            " WHERE item_id = 'o-333ccc' AND project_slug = 'repo.x'"
        ).fetchall()
    finally:
        conn.close()
    assert rows
    assert all(
        v == "receipt:receipt-invalid@2026-08-29T10:00:00Z" for (v,) in rows)


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
    """The reader's guard branches: unknown project and an unreadable ledger
    path both answer [] — a ledger read must never take a rebuild down."""
    assert store.verification_rows(project_dir=None) == []
    slug = store.project_slug("/repo/x")
    path = config.checkpoint_dir() / slug / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # a directory where the file should be -> OSError on open
    assert store.verification_rows(project_dir="/repo/x") == []


def test_non_utf8_ledger_never_kills_the_rebuild(tmp_checkpoint_dir,
                                                 monkeypatch):
    """#836 review crasher: one non-UTF-8 byte in a ledger raised
    UnicodeDecodeError through the fold, killing rebuild() and every search
    after. The reader now shares verification_counts' wider posture; the
    fold survives and the item simply stays unmarked."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-1", _cp("S-1", questions=[
        _item("the axolotl exporter claim was verified", "o-111aaa")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    path = config.checkpoint_dir() / slug / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe not utf-8 at all\n")

    assert store.verification_rows(bucket=path.parent) == []
    hits = recall.search("axolotl exporter claim", project_dir="/repo/x")
    assert hits
    assert hits[0]["invalidated_by"] is None


def test_invalidation_check_names_match_worldchecks_ledger():
    """Divergence guard: recall's filter constant and worldcheck's ledger
    check name are pinned equal rather than imported (recall's import graph
    stays free of worldcheck's probe machinery)."""
    assert recall._INVALIDATION_CHECKS == (worldcheck._LEDGER_CHECK,)


# --- #837: the READ surfaces ------------------------------------------------
#
# #835 populated the slot and #836 shipped it; nothing consumed it, so the
# record existed and changed nothing a user saw. suggest() did not even select
# the column, which is the sharp edge: the auto-inject path re-asserted a claim
# this install's own verification ledger contradicted, at FULL weight, while a
# merely-superseded item was downweighted. These tests pin the invariant the
# issue names: a contradicted item never outranks and never out-renders its
# clean equivalent, on any surface.
#
# The semantics ride along unchanged. The slot holds contradiction EVIDENCE,
# so every surface says "contradicted by <evidence> at <ts>" and no surface
# says "false" — there is no confirmation row and no cure path yet, so a
# present-tense verdict would overclaim what the field can know.


def _decision(text, ref, **extra):
    return {"text": text, "trust": "inferred", "id": ref, **extra}


def test_suggest_ranks_a_contradicted_item_below_its_clean_equivalent(
        tmp_checkpoint_dir, monkeypatch):
    # The fixture is rigged AGAINST the penalty: the contradicted item is the
    # more important one (9 vs 5), so without a demotion it wins outright.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    common = {"first_seen": "2026-08-01T00:00:00Z"}
    store.write_checkpoint("S-bad", _cp("S-bad", decisions=[
        _decision("the axolotl exporter caches every regenerated limb",
                  "o-bad111", importance=9, **common)],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-clean", _cp("S-clean", decisions=[
        _decision("the axolotl exporter caches every regenerated limb",
                  "o-clean1", importance=5, **common)],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-bad111")])

    out = recall.suggest("what did the axolotl exporter do with limb caches",
                         project_dir="/repo/x", current_session="S-now",
                         limit=5, now=1756468800.0)
    sids = [r["session_id"] for r in out]
    assert "S-bad" in sids and "S-clean" in sids
    assert sids.index("S-clean") < sids.index("S-bad")


def test_suggest_carries_the_evidence_out_so_the_line_can_render_it(
        tmp_checkpoint_dir, monkeypatch):
    # Demoting silently is not enough: burial stays VISIBLE, so the row must
    # carry the evidence to the emitter.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-bad", _cp("S-bad", decisions=[
        _decision("the axolotl exporter caches every regenerated limb",
                  "o-bad111")], created="2026-08-01T00:00:00Z"),
        project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-bad111")])

    out = recall.suggest("what did the axolotl exporter do with limb caches",
                         project_dir="/repo/x", current_session="S-now")
    assert out
    assert out[0]["invalidated_by"] == \
        "receipt:receipt-invalid@2026-08-29T10:00:00Z"


def test_suggest_penalties_stack_because_the_axes_are_independent(
        tmp_checkpoint_dir, monkeypatch):
    # superseded_by and invalidated_by are independent facts (#836), so an
    # item carrying BOTH is demoted below one carrying either alone.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    now = 1756468800.0
    row = {"importance": 5, "first_seen": "2026-08-01T00:00:00Z",
           "trust": "inferred"}
    both = recall._suggest_weight(
        {**row, "superseded_by": "S-newer",
         "invalidated_by": "receipt:receipt-invalid@2026-08-29T10:00:00Z"},
        "recent_decision", now)
    superseded_only = recall._suggest_weight(
        {**row, "superseded_by": "S-newer", "invalidated_by": None},
        "recent_decision", now)
    contradicted_only = recall._suggest_weight(
        {**row, "superseded_by": None,
         "invalidated_by": "receipt:receipt-invalid@2026-08-29T10:00:00Z"},
        "recent_decision", now)
    clean = recall._suggest_weight(
        {**row, "superseded_by": None, "invalidated_by": None},
        "recent_decision", now)
    assert both < contradicted_only < superseded_only < clean


def test_search_ranks_a_contradicted_item_below_its_clean_equivalent(
        tmp_checkpoint_dir, monkeypatch):
    # Rigged against the fix again: the contradicted row is the SHORTER
    # document, so bm25 alone puts it first.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-bad", _cp("S-bad", decisions=[
        _decision("axolotl exporter", "o-bad111")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-clean", _cp("S-clean", decisions=[
        _decision("axolotl exporter for the regenerated limb cache pipeline",
                  "o-clean1")], created="2026-08-01T00:00:00Z"),
        project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-bad111")])

    hits = recall.search("axolotl exporter", project_dir="/repo/x")
    sids = [h["session_id"] for h in hits]
    assert "S-bad" in sids and "S-clean" in sids
    assert sids.index("S-clean") < sids.index("S-bad")


def test_search_demotes_contradiction_at_least_as_hard_as_supersession(
        tmp_checkpoint_dir, monkeypatch):
    # #837's wording: "at least as strongly as superseded ones". Contradiction
    # evidence is the stronger claim of the two, so it sorts last.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    # Distinct texts on purpose: _dedupe_rows keys on (kind, author, text),
    # so two items worded identically collapse into one result and the
    # comparison this test exists to make disappears.
    store.write_checkpoint("S-old", _cp("S-old", decisions=[
        _decision("axolotl exporter limb cache rollout", "o-old111")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-newer", _cp("S-newer", decisions=[
        {"text": "axolotl exporter limb cache rewritten", "trust": "inferred",
         "links": [{"type": "supersedes",
                    "target": "axolotl exporter limb cache rollout"}]}],
        created="2026-08-02T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-bad", _cp("S-bad", decisions=[
        _decision("axolotl exporter limb cache probe", "o-bad111")],
        created="2026-08-01T00:00:00Z"), project_dir="/repo/x")
    _write_ledger(store.project_slug("/repo/x"), [_receipt_row("o-bad111")])

    hits = recall.search("axolotl exporter limb cache", project_dir="/repo/x")
    sids = [h["session_id"] for h in hits]
    assert "S-bad" in sids and "S-old" in sids
    assert sids.index("S-old") < sids.index("S-bad")


def test_describe_invalidation_reads_the_stored_encoding():
    # One parse, shared by every renderer, so a marker can never describe a
    # different view than the one the fold wrote.
    assert recall.describe_invalidation(
        "receipt:receipt-invalid@2026-08-29T10:00:00Z") == \
        "contradicted by receipt:receipt-invalid at 2026-08-29T10:00:00Z"


def test_describe_invalidation_never_says_false():
    # The slot is EVIDENCE, not a verdict: no cure path exists, so a
    # present-tense claim would overclaim what the field can know.
    phrase = recall.describe_invalidation(
        "receipt:receipt-invalid@2026-08-29T10:00:00Z")
    assert "false" not in phrase.lower()
    assert "was contradicted" not in phrase  # tense belongs to the caller


def test_describe_invalidation_tolerates_a_malformed_value():
    # A value the fold could not have written still renders as evidence
    # rather than raising on a user's read path.
    assert recall.describe_invalidation("garbage") == "contradicted by garbage"
    assert recall.describe_invalidation(None) is None
    assert recall.describe_invalidation("") is None
