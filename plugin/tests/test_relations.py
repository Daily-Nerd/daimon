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
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
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


# -- shared listing: one presentation fold for CLI and viewer (#678 P3) --

def test_listing_sorts_candidates_first_and_withholds_erased(bucket):
    kept = _propose(bucket)
    relations.confirm(kept, channel="cli-tty", project_dir=bucket)
    second = _propose(bucket, frm=_endpoint("S3", item="r-aaa111222333"),
                      to=_endpoint("S1", item="r-bbb444555666"))
    doomed = _propose(bucket, frm=_endpoint("S4", item="r-ccc777888999"),
                      to=_endpoint("S1", item="r-ddd000111222"))
    store.append_event("r-ccc777888999", "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=bucket)
    rows, withheld = relations.listing(project_dir=bucket)
    ids = [r["relation_id"] for r in rows]
    assert doomed not in ids
    assert withheld == 1
    assert ids == [second, kept]  # candidate first, then confirmed


def test_listing_state_filter_and_unknown_state_refusal(bucket):
    rel_id = _propose(bucket)
    relations.reject(rel_id, channel="cli-tty", project_dir=bucket)
    rows, _ = relations.listing(states={"rejected"}, project_dir=bucket)
    assert [r["relation_id"] for r in rows] == [rel_id]
    with pytest.raises(relations.RelationError):
        relations.listing(states={"vibes"}, project_dir=bucket)


def test_for_item_returns_confirmed_edges_only(bucket):
    confirmed = _propose(bucket)
    relations.confirm(confirmed, channel="cli-tty", project_dir=bucket)
    _propose(bucket, frm=_endpoint("S3", item="r-abc123456789"),
             to=_endpoint("S1", item="r-bbb444555666"))  # stays candidate
    rows, withheld = relations.for_item("r-abc123456789", project_dir=bucket)
    assert [r["relation_id"] for r in rows] == [confirmed]
    assert withheld == 0
    other, _ = relations.for_item("r-feedbeef1234", project_dir=bucket)
    assert other == []


def test_for_item_withholds_chains_touching_erased_endpoints(bucket):
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    store.append_event("r-def123456789", "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=bucket)
    rows, withheld = relations.for_item("r-abc123456789", project_dir=bucket)
    assert rows == [] and withheld == 1


def test_endpoint_texts_joins_over_project_surfaces(bucket):
    from daimon_briefing import policy
    cp = {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
          "project_slug": store.project_slug(bucket),
          "working_context": {"recent_decisions": [
              {"text": "keep the fold deterministic", "trust": "inferred"}]}}
    policy.stamp_item_ids(cp)
    store.write_checkpoint("S1", cp, project_dir=bucket)
    item_id = cp["working_context"]["recent_decisions"][0]["id"]
    texts = relations.endpoint_texts(project_dir=bucket)
    assert texts[item_id] == "keep the fold deterministic"


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
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    results = privacy.audit_project(project_dir=bucket)
    assert results["relations"]["rows"] == 2
    assert results["relations"]["records"] == 1
    assert results["relations"]["by_state"] == {"confirmed": 1}
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

# -- defensive branches and forged-row hardening --

def test_unknown_project_refuses_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path))
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
    with pytest.raises(relations.RelationError):
        _propose(None)


def test_kill_switch_blocks_appends(bucket, monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    with pytest.raises(relations.RelationError):
        _propose(bucket)


def test_make_id_refuses_unknown_type():
    with pytest.raises(relations.RelationError):
        relations.make_id("friends-with", _endpoint(), _endpoint())


def test_stamp_refuses_bad_event_id_and_channel():
    good_id = "rel-" + "a" * 16
    with pytest.raises(relations.RelationError):
        relations._stamp("exploded", good_id, "cli-tty")
    with pytest.raises(relations.RelationError):
        relations._stamp("proposed", "not-an-id", "cli-tty")
    with pytest.raises(relations.RelationError):
        relations._stamp("proposed", good_id, "carrier-pigeon")


def test_endpoint_must_be_a_dict(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, frm="r-abc123456789")


def test_rails_are_required_and_capped(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, rails=())
    with pytest.raises(relations.RelationError):
        _propose(bucket, rails=("carry-absolute",) * 9)


def test_matcher_version_shape_is_refused(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, matcher="Lineage V1!")


def test_unknown_relation_type_is_refused_at_propose(bucket):
    with pytest.raises(relations.RelationError):
        _propose(bucket, type_="friends-with")


def test_oversized_row_is_refused_loudly_not_silently(bucket):
    with pytest.raises(relations.RelationError):
        relations._write({"pad": "x" * 3000}, project_dir=bucket)


def test_is_torn_survives_a_missing_file(tmp_path):
    assert relations._is_torn(tmp_path / "absent.jsonl") is False


def test_verdicts_on_unknown_relation_are_refused(bucket):
    for move in (relations.confirm, relations.reject, relations.retract):
        with pytest.raises(relations.RelationError):
            move("rel-" + "f" * 16, channel="cli-tty", project_dir=bucket)


def test_verdicts_on_retracted_record_are_refused_at_the_api(bucket):
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    relations.retract(rel_id, channel="cli-tty", project_dir=bucket)
    with pytest.raises(relations.RelationError):
        relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    with pytest.raises(relations.RelationError):
        relations.reject(rel_id, channel="cli-tty", project_dir=bucket)


def test_orphan_lifecycle_event_is_inert(bucket):
    row = relations._stamp("confirmed", "rel-" + "a" * 16, "cli-tty")
    assert relations._append(row, project_dir=bucket)
    assert relations.records(project_dir=bucket) == {}


def test_forged_verdict_on_agent_channel_cannot_move_state(bucket):
    # Bypass the API guard entirely: a script appending a raw `confirmed`
    # row on an agent channel must still fold to candidate.
    rel_id = _propose(bucket)
    row = relations._stamp("confirmed", rel_id, "lab-import")
    assert relations._append(row, project_dir=bucket)
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "candidate"


def test_forged_verdicts_on_retracted_record_stay_inert_in_fold(bucket):
    rel_id = _propose(bucket)
    relations.confirm(rel_id, channel="cli-tty", project_dir=bucket)
    relations.retract(rel_id, channel="cli-tty", project_dir=bucket)
    for event in ("confirmed", "rejected"):
        row = relations._stamp(event, rel_id, "cli-tty")
        assert relations._append(row, project_dir=bucket)
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "retracted"


def test_reject_then_human_confirm_overrides(bucket):
    rel_id = _propose(bucket)
    relations.reject(rel_id, channel="cli-tty", project_dir=bucket)
    relations.confirm(rel_id, channel="ui", project_dir=bucket)
    assert relations.records(project_dir=bucket)[rel_id]["state"] == "confirmed"


def test_contradiction_pass_skips_symmetric_and_forged_types(bucket):
    x = _endpoint("S1", item="r-abc123456789")
    y = _endpoint("S2", item="r-def123456789")
    arc = _propose(bucket, type_="same-arc", frm=x, to=y)
    relations.confirm(arc, channel="cli-tty", project_dir=bucket)
    forged = relations._stamp("proposed", "rel-" + "b" * 16, "lab-import")
    forged.update({"type": "friends-with", "from": x, "to": y,
                   "matched_by": ["carry-absolute"],
                   "matcher_version": "lineage-v1"})
    assert relations._append(forged, project_dir=bucket)
    confirm_row = relations._stamp("confirmed", "rel-" + "b" * 16, "cli-tty")
    assert relations._append(confirm_row, project_dir=bucket)
    folded = relations.records(project_dir=bucket)
    assert folded[arc]["contradiction"] is False
    assert folded["rel-" + "b" * 16]["contradiction"] is False


def test_contradiction_pass_survives_damaged_endpoints(bucket):
    damaged = relations._stamp("proposed", "rel-" + "c" * 16, "lab-import")
    damaged.update({"type": "revision-of", "from": {}, "to": {},
                    "matched_by": ["carry-absolute"],
                    "matcher_version": "lineage-v1"})
    assert relations._append(damaged, project_dir=bucket)
    confirm_row = relations._stamp("confirmed", "rel-" + "c" * 16, "cli-tty")
    assert relations._append(confirm_row, project_dir=bucket)
    folded = relations.records(project_dir=bucket)
    assert folded["rel-" + "c" * 16]["contradiction"] is False


def test_reader_and_forget_survive_unreadable_ledger(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    path.chmod(0)
    try:
        assert relations.events(project_dir=bucket) == []
        assert relations.forget_item_id(
            "r-abc123456789", project_dir=bucket) == []
    finally:
        path.chmod(0o600)


def test_forget_empty_target_absent_ledger_and_no_match(bucket):
    assert relations.forget_item_id("r-abc123456789",
                                    project_dir=bucket) == []
    _propose(bucket)
    assert relations.forget_item_id("", project_dir=bucket) == []
    assert relations.forget_item_id("r-ffffffffffff",
                                    project_dir=bucket) == []


def test_reopen_lifts_the_tombstone(bucket):
    store.append_event("r-abc123456789", "forgotten:deadbeef01234567",
                       kind="tombstone", project_dir=bucket)
    store.append_event("r-abc123456789", "reopen", project_dir=bucket)
    assert "r-abc123456789" not in relations.tombstoned_item_ids(
        project_dir=bucket)


def test_audit_reports_unreadable_relations_ledger_as_unscannable(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    path.chmod(0)
    try:
        results = privacy.audit_project(project_dir=bucket)
        assert any("relations.jsonl" in entry
                   for entry in results["unscannable"])
    finally:
        path.chmod(0o600)


def test_audit_counts_skip_garbage_lines(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n42\n")
    results = privacy.audit_project(project_dir=bucket)
    assert results["relations"]["rows"] == 1


def test_cli_forget_scrubs_relations_and_reports_them(
        tmp_checkpoint_dir, monkeypatch, capsys):
    from daimon_briefing import cli
    project = "/repo/relations-forget-arc"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    store.write_checkpoint("S1", {
        "session_id": "S1",
        "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [
                {"text": "adopt sqlite for the relations cache",
                 "trust": "inferred"}]},
    }, project_dir=project)
    stored = store.read_latest(project_dir=project, fallback=False)
    item_id = stored["working_context"]["recent_decisions"][0]["id"]
    rel_id = relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S1", "field": "recent_decisions",
                       "item_id": item_id},
        to_endpoint=_endpoint("S0", item="r-def123456789"),
        matched_by=["carry-absolute"],
        matcher_version="lineage-v1",
        channel="lab-import",
        project_dir=project,
    )
    assert cli.main(["forget", item_id]) == 0
    out = capsys.readouterr().out
    assert "relation(s)" in out and rel_id in out
    assert relations.records(project_dir=project) == {}


def test_make_id_canonicalizes_symmetric_endpoints_directly():
    x = _endpoint("S1", item="r-abc123456789")
    y = _endpoint("S2", item="r-def123456789")
    assert relations.make_id("same-arc", y, x) == relations.make_id(
        "same-arc", x, y)


def test_append_survives_unwritable_ledger(bucket):
    _propose(bucket)
    path = relations._path(bucket)
    path.chmod(0o400)
    try:
        row = relations._stamp("proposed", "rel-" + "d" * 16, "lab-import")
        assert relations._append(row, project_dir=bucket) is False
    finally:
        path.chmod(0o600)


def test_reader_and_forget_return_empty_for_unknown_project(tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path))
    assert relations.events(project_dir=None) == []
    assert relations.forget_item_id("r-abc123456789", project_dir=None) == []


def test_forget_rewrite_drops_blank_and_unparseable_lines(bucket):
    doomed = _propose(bucket)
    kept = _propose(bucket, frm=_endpoint("S3", item="r-aaa111222333"),
                    to=_endpoint("S1", item="r-bbb444555666"))
    path = relations._path(bucket)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n{broken json\n")
    assert relations.forget_item_id("r-abc123456789",
                                    project_dir=bucket) == [doomed]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(line.strip() for line in lines)
    assert kept in relations.records(project_dir=bucket)


def test_forget_rewrite_failure_returns_empty_not_partial(bucket, monkeypatch):
    _propose(bucket)
    path = relations._path(bucket)
    original = type(path).write_text

    def failing_write(self, *args, **kwargs):
        if self.name.endswith(".forget-tmp"):
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "write_text", failing_write)
    assert relations.forget_item_id("r-abc123456789",
                                    project_dir=bucket) == []
    # the ledger is untouched: atomic-or-nothing
    assert relations.records(project_dir=bucket) != {}


def test_forget_read_failure_after_doomed_scan_returns_empty(bucket,
                                                             monkeypatch):
    _propose(bucket)
    path = relations._path(bucket)
    original = type(path).read_text
    calls = {"n": 0}

    def flaky_read(self, *args, **kwargs):
        if self.name == "relations.jsonl":
            calls["n"] += 1
            if calls["n"] > 1:      # events() scan works, rewrite read fails
                raise OSError("io error")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", flaky_read)
    assert relations.forget_item_id("r-abc123456789",
                                    project_dir=bucket) == []


def test_only_declared_consumers_import_relations():
    # Two-directional: a new production import fails (inertness breach), and a
    # stale allowlist entry fails (the twin that keeps this test non-vacuous).
    import pathlib
    import re

    import daimon_briefing
    package = pathlib.Path(daimon_briefing.__file__).parent
    allowed = {"privacy.py", "cli/__init__.py"}
    import_re = re.compile(
        r"^\s*(?:from\s+\.\.?\s+import\s+.*\brelations\b"
        r"|from\s+(?:daimon_briefing\.)?relations\s+import"
        r"|import\s+(?:daimon_briefing\.)?relations\b)", re.MULTILINE)
    importers = set()
    for module in package.rglob("*.py"):
        if module.name == "relations.py":
            continue
        if import_re.search(module.read_text(encoding="utf-8")):
            importers.add(module.relative_to(package).as_posix())
    assert importers == allowed
