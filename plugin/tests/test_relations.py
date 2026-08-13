"""Relations ledger (#678 Phase 2): validation gates, fold, deletion, registry.

Spec: vault "Spec - Relations Ledger Phase 2 (678)" v2 (post-refutation,
fork A ratified: forget reaches this file; audit scans it).
"""

import json

import pytest

from daimon_briefing import privacy, relations, store, surfaces


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.delenv("DAIMON_DISABLED", raising=False)
    return "/repo/relations-fixture"


def _endpoint(session="S1", field="recent_decisions", item="r-abc123456789"):
    return {"session_id": session, "field": field, "item_id": item}


def _propose(project, *, type_="revision-of", frm=None, to=None,
             channel="lab-import", rails=("carry-absolute",),
             matcher="lineage-v1", now_ns=None):
    return relations.propose(
        type_=type_,
        from_endpoint=frm or _endpoint("S2", item="r-abc123456789"),
        to_endpoint=to or _endpoint("S1", item="r-def123456789"),
        matched_by=list(rails),
        matcher_version=matcher,
        channel=channel,
        project_dir=project,
        now_ns=now_ns,
    )


# -- validation gates: every writable string, refusal on any failure --

def test_propose_creates_candidate_record(bucket):
    rel_id = _propose(bucket)
    record = relations.records(project_dir=bucket)[rel_id]
    assert record["state"] == "candidate"
    assert record["type"] == "revision-of"
    assert record["proposals"][0]["matcher_version"] == "lineage-v1"


def test_refuses_prose_session_id(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, frm=_endpoint(session="sync for acme, key AKIA123"))


def test_refuses_colon_session_id(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, frm=_endpoint(session="a:b"))


def test_refuses_field_outside_item_lists(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, frm=_endpoint(field="the item text leaked here"))


def test_refuses_text_shaped_item_id(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, frm=_endpoint(item="use mutable memory records"))


def test_refuses_unknown_rail(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, rails=("vibes",))


def test_refuses_unknown_note_code(bucket):
    with pytest.raises(relations.RelationError):
        relations.propose(
            type_="revision-of",
            from_endpoint=_endpoint("S2"),
            to_endpoint=_endpoint("S1", item="r-def123456789"),
            matched_by=["carry-absolute"],
            matcher_version="lineage-v1",
            channel="lab-import",
            note_code="free prose",
            project_dir=bucket,
        )


def test_accepts_every_minted_item_id_shape(bucket):
    # width ladder + twin counter + legacy 6-hex era
    for item in ("o-abc123", "s-" + "a" * 16, "u-" + "b" * 24,
                 "c-" + "c" * 40, "r-abc123456789-2"):
        relations.propose(
            type_="revision-of",
            from_endpoint=_endpoint("S2", field="open_questions", item=item),
            to_endpoint=_endpoint("S1", item="r-def123456789"),
            matched_by=["carry-absolute"],
            matcher_version="lineage-v1",
            channel="lab-import",
            project_dir=bucket,
        )


# -- relation_id: determinism, symmetry, direction --

def test_same_edge_mints_same_id_and_folds_to_one_record(bucket):
    a = _propose(bucket)
    b = _propose(bucket)
    assert a == b
    assert len(relations.records(project_dir=bucket)) == 1


def test_same_arc_is_symmetric_one_fact_one_id(bucket):
    x, y = _endpoint("S1", item="r-abc123456789"), _endpoint("S2", item="r-def123456789")
    a = _propose(bucket, type_="same-arc", frm=x, to=y)
    b = _propose(bucket, type_="same-arc", frm=y, to=x)
    assert a == b


def test_directional_types_keep_direction_distinct(bucket):
    x, y = _endpoint("S1", item="r-abc123456789"), _endpoint("S2", item="r-def123456789")
    assert _propose(bucket, frm=x, to=y) != _propose(bucket, frm=y, to=x)


def test_field_is_display_data_not_identity(bucket):
    a = _propose(bucket, frm=_endpoint("S2", field="recent_decisions"))
    b = _propose(bucket, frm=_endpoint("S2", field="open_questions"))
    assert a == b


# -- authority: agents propose, only humans move state --

def test_lab_import_channel_cannot_confirm(bucket):
    rel_id = _propose(bucket)
    with pytest.raises(relations.RelationError):
        relations.confirm(rel_id, channel="lab-import", project_dir=bucket)


def test_confirm_requires_reachable_human_channel(bucket):
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "confirmed"


def test_rejected_is_sticky_against_agent_reproposal(bucket):
    rel_id = _propose(bucket)
    relations.reject(rel_id, channel="cli-tty", project_dir=bucket)
    _propose(bucket, matcher="lineage-v2")
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "rejected"


def test_retracted_is_revived_by_fresh_proposal(bucket):
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    relations.retract(rel_id, channel="cli-tty", project_dir=bucket)
    _propose(bucket, matcher="lineage-v2")
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "candidate"


def test_proposals_accrue_per_matcher_version(bucket):
    rel_id = _propose(bucket, matcher="lineage-v1", rails=("carry-absolute",))
    _propose(bucket, matcher="lineage-v2", rails=("bound-exact-quote",))
    proposals = relations.records(project_dir=bucket)[rel_id]["proposals"]
    assert [p["matcher_version"] for p in proposals] == ["lineage-v1", "lineage-v2"]
    assert proposals[1]["matched_by"] == ["bound-exact-quote"]


# -- fold: tie safety, order hygiene, contradiction --

def test_equal_order_confirm_reject_tie_lands_on_rejected(bucket):
    rel_id = _propose(bucket, now_ns=1_000)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket, now_ns=2_000)
    relations.reject(rel_id, channel="ui", project_dir=bucket, now_ns=2_000)
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "rejected"


def test_rows_without_usable_order_are_dropped_by_reader(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    row = json.loads(path.read_text().splitlines()[0])
    row["order"] = "garbage"
    row["event_id"] = "f" * 32
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    assert len(relations.events(project_dir=bucket)) == 1


def test_inverse_confirmed_revisions_are_flagged_never_resolved(bucket):
    x, y = _endpoint("S1", item="r-abc123456789"), _endpoint("S2", item="r-def123456789")
    a = _propose(bucket, frm=x, to=y)
    b = _propose(bucket, frm=y, to=x)
    relations.confirm(a, channel="cli-tty", project_dir=bucket)
    relations.confirm(b, channel="cli-tty", project_dir=bucket)
    folded = relations.records(project_dir=bucket)
    assert folded[a]["contradiction"] is True
    assert folded[b]["contradiction"] is True
    assert folded[a]["state"] == "confirmed"  # surfaced, not auto-resolved


def test_torn_append_costs_exactly_the_torn_row(bucket):
    rel_id = _propose(bucket)
    path = relations._path(bucket)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"torn": tru')
    _propose(bucket, frm=_endpoint("S3", item="r-aaa111222333"))
    assert len(relations.records(project_dir=bucket)) == 2
    assert rel_id in relations.records(project_dir=bucket)


# -- deletion (fork A): forget reaches this file --

def test_forget_item_id_rewrites_only_matching_rows(bucket):
    doomed = _propose(bucket, frm=_endpoint("S2", item="r-abc123456789"))
    kept = _propose(bucket, frm=_endpoint("S2", item="r-aaa111222333"),
                    to=_endpoint("S1", item="r-bbb444555666"))
    removed = relations.forget_item_id("r-abc123456789", project_dir=bucket)
    assert removed == [doomed]
    folded = relations.records(project_dir=bucket)
    assert doomed not in folded and kept in folded


def test_forget_item_id_keeps_uninterpretable_rows_byte_identical(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    alien = '{"event": "from-the-future", "relation_id": "rel-' + "a" * 16 + '"}'
    with path.open("a", encoding="utf-8") as handle:
        handle.write(alien + "\n")
    relations.forget_item_id("r-abc123456789", project_dir=bucket)
    assert alien in path.read_text().splitlines()


# -- erased vs unresolved dangling: tombstone-derived, GC-immune --

def test_erased_comes_from_tombstones_not_absence(bucket):
    store.append_event("r-abc123456789", "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=bucket)
    erased = relations.tombstoned_item_ids(project_dir=bucket)
    assert "r-abc123456789" in erased
    # absent-but-never-tombstoned is NOT erased
    assert "r-def123456789" not in erased


# -- registry (fork A) --

def test_registry_declares_relations_ledger_plaintext_rewrite_forget():
    entry = surfaces.match("checkpoints/{slug}/relations.jsonl")
    assert entry is not None
    assert entry.plaintext is True
    assert entry.delete == "rewrite"
    assert entry.walker == "forget"
    assert entry.audit_exempt is False


def test_exempt_no_plaintext_bucket_ledgers_are_audit_exempt():
    # The biconditional the refuters proved missing: an exempt-no-plaintext
    # bucket ledger that is not audit_exempt lands in the unknown walk and
    # pins the audit at permanent exit 3 (#645's actual hole).
    for s in surfaces.SURFACES:
        if (s.shape.startswith("checkpoints/{slug}/")
                and s.delete == "exempt-no-plaintext"):
            assert s.audit_exempt, s.shape


# -- audit: counts + tombstone-intersection findings, never unknown --

def test_audit_reports_relations_counts_and_stays_provable(bucket):
    _propose(bucket)
    results = privacy.audit_project(project_dir=bucket)
    assert results["relations"]["rows"] == 1
    assert results["relations"]["records"] == 1
    unknown = [entry for entry in results.get("unscannable", [])
               if "relations.jsonl" in str(entry)]
    assert unknown == []


def test_audit_finds_residue_when_scrub_missed_a_tombstoned_endpoint(bucket):
    _propose(bucket)  # endpoint r-abc123456789 lands in the ledger
    store.append_event("r-abc123456789", "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=bucket)
    results = privacy.audit_project(project_dir=bucket)
    hits = [f for f in results["findings"]
            if f.get("surface") == "relations-ledger"]
    assert hits, "tombstoned endpoint surviving in relations.jsonl must be residue"
    # and after the scrub runs, the audit proves the deletion
    relations.forget_item_id("r-abc123456789", project_dir=bucket)
    results = privacy.audit_project(project_dir=bucket)
    assert [f for f in results["findings"]
            if f.get("surface") == "relations-ledger"] == []


# -- inertness: consumers do not read this ledger (with a live ratchet) --

def test_only_declared_consumers_import_relations():
    # Two-directional: a new production import fails (inertness breach), and a
    # stale allowlist entry fails (the twin that keeps this test non-vacuous).
    import pathlib
    import re

    import daimon_briefing
    package = pathlib.Path(daimon_briefing.__file__).parent
    allowed = {"privacy.py", "cli.py"}
    import_re = re.compile(
        r"^\s*(?:from\s+\.\s+import\s+.*\brelations\b"
        r"|from\s+(?:daimon_briefing\.)?relations\s+import"
        r"|import\s+(?:daimon_briefing\.)?relations\b)", re.MULTILINE)
    importers = set()
    for module in package.glob("*.py"):
        if module.name == "relations.py":
            continue
        if import_re.search(module.read_text(encoding="utf-8")):
            importers.add(module.name)
    assert importers == allowed
