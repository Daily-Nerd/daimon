"""Human-only quarantine ledger (#1109 Slice 1, write-only — nothing reads it
yet; that is PR 2).

An agent may PROPOSE a quarantine; only a human channel may CONFIRM, DISMISS,
or RELEASE one, enforced in the library write functions themselves (not just
the CLI) — mirroring how `refutations.py` gates `ratify`. Identity is
VALUE-keyed via the same `normalize.content_key` `forget` uses, scoped by
`(kind, scope_slug)`, so a carried or re-extracted copy of the same text
under a different item id stays quarantined.
"""

import pytest

from daimon_briefing import trust

VALUE = "the deploy key rotation runbook was fabricated by the agent"
ITEM = "o-1234567890ab"


def _propose(project, *, channel="cli-agent", text=VALUE, kind="decision",
             reason="looks fabricated, no matching PR", evidence=None,
             item_id=ITEM, **kwargs):
    return trust.propose(
        text=text, kind=kind, reason=reason,
        evidence=["issue:1109"] if evidence is None else evidence,
        channel=channel, item_id=item_id, project_dir=project, **kwargs)


@pytest.fixture
def project(tmp_checkpoint_dir):
    # conftest's autouse fixture already isolates DAIMON_CHECKPOINT_DIR.
    return "/p/A"


# ---- propose / channel doctrine --------------------------------------------


def test_agent_propose_lands_as_candidate(project):
    tid = _propose(project)
    record = trust.get(tid, project_dir=project)
    assert record["state"] == "candidate"
    assert record["kind"] == "decision"
    assert record["item_id"] == ITEM
    assert record["reason"] == "looks fabricated, no matching PR"
    assert record["evidence"] == ["issue:1109"]
    assert record["proposed_by"] == "agent"
    assert record["value_key"] == trust.value_key(VALUE)


def test_human_tty_propose_lands_active_immediately(project):
    tid = _propose(project, channel="cli-tty")
    record = trust.get(tid, project_dir=project)
    assert record["state"] == "active"
    assert record["activation_channel"] == "cli-tty"


def test_ui_channel_propose_lands_active(project):
    tid = _propose(project, channel="ui")
    assert trust.get(tid, project_dir=project)["state"] == "active"


def test_unknown_kind_refused(project):
    with pytest.raises(trust.TrustError):
        _propose(project, kind="not-a-real-kind")


def test_every_schema_kind_is_a_valid_kind(project):
    assert trust.KINDS == {
        "topic", "question", "decision", "belief", "uncertainty",
        "contradiction"}


def test_missing_evidence_refused(project):
    with pytest.raises(trust.TrustError):
        _propose(project, evidence=[])


def test_malformed_evidence_source_refused(project):
    with pytest.raises(trust.TrustError):
        _propose(project, evidence=["not-typed-text"])


def test_missing_reason_refused(project):
    with pytest.raises(trust.TrustError):
        _propose(project, reason="")


def test_short_value_text_refused(project):
    """#1109 design: a short, generic value risks quarantining unrelated
    items that canonicalize the same after folding."""
    with pytest.raises(trust.TrustError):
        _propose(project, text="done")


def test_duplicate_propose_refused_while_candidate(project):
    _propose(project)
    with pytest.raises(trust.TrustError):
        _propose(project)


def test_same_text_different_kind_does_not_collide(project):
    """#1109 design §2: (kind, scope_slug) scoping — a belief and a decision
    that happen to read the same text must mint different ids."""
    tid_decision = _propose(project, kind="decision")
    tid_belief = _propose(project, kind="belief")
    assert tid_decision != tid_belief
    assert trust.get(tid_decision, project_dir=project)["state"] == "candidate"
    assert trust.get(tid_belief, project_dir=project)["state"] == "candidate"


def test_same_text_different_project_does_not_collide(tmp_checkpoint_dir):
    tid_a = _propose("/p/A")
    tid_b = _propose("/p/B")
    assert tid_a != tid_b


# ---- confirm / dismiss / release: human-only, structurally enforced -------


def test_agent_channel_cannot_confirm(project):
    tid = _propose(project)
    with pytest.raises(trust.TrustError):
        trust.confirm(tid, channel="cli-agent", project_dir=project)
    assert trust.get(tid, project_dir=project)["state"] == "candidate"


def test_agent_channel_cannot_dismiss(project):
    tid = _propose(project)
    with pytest.raises(trust.TrustError):
        trust.dismiss(tid, channel="cli-agent", project_dir=project)
    assert trust.get(tid, project_dir=project)["state"] == "candidate"


def test_agent_channel_cannot_release(project):
    tid = _propose(project, channel="cli-tty")
    with pytest.raises(trust.TrustError):
        trust.release(tid, channel="cli-agent", project_dir=project)
    assert trust.get(tid, project_dir=project)["state"] == "active"


def test_human_confirm_promotes_candidate(project):
    tid = _propose(project)
    trust.confirm(tid, channel="cli-tty", project_dir=project)
    record = trust.get(tid, project_dir=project)
    assert record["state"] == "active"
    assert record["activation_channel"] == "cli-tty"


def test_human_dismiss_a_candidate(project):
    tid = _propose(project)
    trust.dismiss(tid, channel="cli-tty", project_dir=project)
    assert trust.get(tid, project_dir=project)["state"] == "dismissed"


def test_cannot_confirm_an_already_active_quarantine(project):
    tid = _propose(project, channel="cli-tty")
    with pytest.raises(trust.TrustError):
        trust.confirm(tid, channel="cli-tty", project_dir=project)


def test_cannot_dismiss_an_active_quarantine(project):
    """Dismiss is for candidates; an active quarantine is lifted by release."""
    tid = _propose(project, channel="cli-tty")
    with pytest.raises(trust.TrustError):
        trust.dismiss(tid, channel="cli-tty", project_dir=project)


def test_human_release_lifts_an_active_quarantine(project):
    tid = _propose(project, channel="cli-tty")
    trust.release(tid, channel="cli-tty", project_dir=project)
    assert trust.get(tid, project_dir=project)["state"] == "released"


def test_cannot_release_a_candidate(project):
    tid = _propose(project)
    with pytest.raises(trust.TrustError):
        trust.release(tid, channel="cli-tty", project_dir=project)


def test_unknown_id_refused_for_every_verdict(project):
    for verb in (trust.confirm, trust.dismiss, trust.release):
        with pytest.raises(trust.TrustError):
            verb("tr-0000000000ab", channel="cli-tty", project_dir=project)


def test_dismissed_value_can_be_reproposed(project):
    tid = _propose(project)
    trust.dismiss(tid, channel="cli-tty", project_dir=project)
    tid2 = _propose(project)
    assert tid2 == tid  # same (kind, scope_slug, value_key) identity
    assert trust.get(tid2, project_dir=project)["state"] == "candidate"


def test_released_value_can_be_requarantined(project):
    tid = _propose(project, channel="cli-tty")
    trust.release(tid, channel="cli-tty", project_dir=project)
    tid2 = _propose(project, channel="cli-tty")
    assert tid2 == tid
    assert trust.get(tid2, project_dir=project)["state"] == "active"


# ---- structural: no agent-facing surface can confirm/release --------------


def test_no_confirm_dismiss_release_path_grants_agent_authority():
    """Structural pin, mirroring `test_write_audit_guard.py`'s style: every
    CHANNEL_AUTHORITY entry `confirm`/`dismiss`/`release` will accept is
    human-tier. If a future channel is added to `trust.CHANNEL_AUTHORITY`
    with any other tier, this fails without needing a live write."""
    for channel, tier in trust.CHANNEL_AUTHORITY.items():
        if tier != "human":
            with pytest.raises(trust.TrustError):
                trust._human_transition(
                    "confirmed", "tr-does-not-matter", channel, "/p/A", None)


# ---- value-keying: survives carry / re-extraction under a new item id -----


def test_active_value_keys_reflects_only_active_records(project):
    tid_a = _propose(project, channel="cli-tty")
    tid_b = _propose(project, text="a completely different quarantined value")
    keys = trust.active_value_keys(project_dir=project)
    record_a = trust.get(tid_a, project_dir=project)
    record_b = trust.get(tid_b, project_dir=project)
    assert (record_a["kind"], record_a["value_key"]) in keys
    assert (record_b["kind"], record_b["value_key"]) not in keys


def test_active_value_keys_is_scoped_by_kind(project):
    """A decision and a belief that canonicalize identically are two
    distinct quarantines (design §2); one being active must not make the
    other's (kind, value_key) pair appear active."""
    tid_decision = _propose(project, kind="decision", channel="cli-tty")
    tid_belief = _propose(project, kind="belief")  # candidate
    keys = trust.active_value_keys(project_dir=project)
    decision_key = trust.get(tid_decision, project_dir=project)["value_key"]
    belief_key = trust.get(tid_belief, project_dir=project)["value_key"]
    assert decision_key == belief_key  # same text, scoped apart by kind
    assert ("decision", decision_key) in keys
    assert ("belief", belief_key) not in keys


def test_value_key_is_stable_across_different_item_ids(project):
    """The whole point of value-keying (#1109 design §2): the SAME text
    proposed under two different item ids (a carried copy, a re-extracted
    twin) resolves to the identical quarantine id."""
    tid_1 = _propose(project, item_id="o-1111111111ab")
    trust.dismiss(tid_1, channel="cli-tty", project_dir=project)
    tid_2 = _propose(project, item_id="o-2222222222ab")
    assert tid_1 == tid_2


def test_value_key_matches_normalize_content_key():
    from daimon_briefing import normalize
    assert trust.value_key(VALUE) == normalize.content_key(VALUE)


# ---- forget reachability: reason/evidence are plaintext --------------------


def test_forget_content_key_removes_the_record(project):
    tid = _propose(project, reason="a very specific fabricated claim here")
    key = trust.row_content_keys(
        {"reason": "a very specific fabricated claim here"})
    removed = trust.forget_content_key(next(iter(key)), project_dir=project)
    assert removed == [tid]
    assert trust.get(tid, project_dir=project) is None


def test_forget_content_key_no_match_removes_nothing(project):
    _propose(project)
    from daimon_briefing import normalize
    removed = trust.forget_content_key(
        normalize.content_key("nothing matches this"), project_dir=project)
    assert removed == []


def test_plaintext_values_reports_reason_not_value_key(project):
    tid = _propose(project)
    record = trust.get(tid, project_dir=project)
    row = {"reason": record["reason"], "evidence": record["evidence"],
           "value_key": record["value_key"]}
    values = trust.plaintext_values(row)
    assert record["reason"] in values
    assert record["value_key"] not in values


def test_row_content_keys_covers_reason_and_evidence():
    from daimon_briefing import normalize
    row = {"reason": "alpha reason text", "evidence": ["issue:1"]}
    keys = trust.row_content_keys(row)
    assert normalize.content_key("alpha reason text") in keys
    assert normalize.content_key("issue:1") in keys


# ---- disabled / unknown project fail closed --------------------------------


def test_disabled_daimon_refuses_write(project, monkeypatch):
    from daimon_briefing import config
    monkeypatch.setattr(config, "is_disabled", lambda: True)
    with pytest.raises(trust.TrustError):
        _propose(project)


def test_unresolvable_project_refuses_write(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import config
    monkeypatch.setattr(config, "resolve_project_dir", lambda p=None: p)
    monkeypatch.setattr("daimon_briefing.store.project_slug", lambda p: None)
    with pytest.raises(trust.TrustError):
        _propose("/p/unresolvable")


# ---- registrations: bucket root + surface + migration ----------------------


def test_propose_stamps_the_bucket_root(project, tmp_checkpoint_dir):
    from daimon_briefing import config, store
    _propose(project)
    slug = store.project_slug(config.resolve_project_dir(project))
    root = config.checkpoint_dir() / slug / "root"
    assert root.exists()


def test_trust_jsonl_is_declared_in_surfaces():
    from daimon_briefing import surfaces
    shapes = {s.shape for s in surfaces.SURFACES}
    assert "checkpoints/{slug}/trust.jsonl" in shapes


def test_trust_jsonl_is_a_ledger_for_bucket_migration():
    from daimon_briefing import buckets
    assert "trust.jsonl" in buckets.LEDGERS


def test_trust_jsonl_is_not_removable_by_migration():
    """A real ledger a legacy-bucket merge must MIGRATE, never a marker a
    merge may discard (#1109 design §1, scar 0101 item 2)."""
    from daimon_briefing import buckets
    assert "trust.jsonl" not in buckets._REMOVABLE


# ---- internals: direct unit tests, mirroring test_refutations.py's own
# style of testing private helpers (`_text`, `_evidence`, `_stamp`, `_path`)
# directly rather than only through the public write verbs.


def test_text_over_cap_raises_trust_too_long():
    with pytest.raises(trust.TrustTooLong) as exc_info:
        trust._text("reason", "x" * (trust._MAX_TEXT + 1))
    assert exc_info.value.field == "reason"
    assert exc_info.value.limit == trust._MAX_TEXT


def test_reason_over_cap_via_propose_raises_too_long(project):
    with pytest.raises(trust.TrustTooLong):
        _propose(project, reason="x" * (trust._MAX_TEXT + 1))


def test_evidence_too_many_sources_refused():
    with pytest.raises(trust.TrustError):
        trust._evidence([f"issue:{i}" for i in range(trust._MAX_EVIDENCE + 1)])


def test_path_returns_none_for_an_unresolvable_project(monkeypatch):
    from daimon_briefing import store
    monkeypatch.setattr(store, "project_slug", lambda p: None)
    assert trust._path("/p/anything") is None


def test_stamp_rejects_unknown_event():
    with pytest.raises(trust.TrustError):
        trust._stamp("deleted", "tr-000000000000", "cli-tty")


def test_stamp_rejects_malformed_id():
    with pytest.raises(trust.TrustError):
        trust._stamp("quarantined", "not-an-id", "cli-tty")


def test_stamp_rejects_unknown_channel():
    with pytest.raises(trust.TrustError):
        trust._stamp("quarantined", "tr-000000000000", "narrator")


def test_is_torn_false_for_a_missing_path():
    from pathlib import Path
    assert trust._is_torn(Path("/does/not/exist/trust.jsonl")) is False


def test_append_returns_false_for_an_unresolvable_project(monkeypatch):
    from daimon_briefing import store
    monkeypatch.setattr(store, "project_slug", lambda p: None)
    assert trust.append({"event": "quarantined"}, project_dir="/p/x") is False


def test_append_returns_false_on_an_oserror(project, monkeypatch):
    from pathlib import Path

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    assert trust.append({"event": "quarantined"}, project_dir=project) is False


def test_events_skips_a_json_parse_failure(project, tmp_checkpoint_dir):
    from daimon_briefing import config, store
    _propose(project)
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    rows = trust.events(project_dir=project)
    assert len(rows) == 1


def test_events_skips_wrong_event_and_malformed_id(project, tmp_checkpoint_dir):
    import json as _json
    from daimon_briefing import config, store
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps({"event": "not-a-real-event",
                                  "quarantine_id": "tr-000000000000"}) + "\n")
        handle.write(_json.dumps({"event": "quarantined",
                                  "quarantine_id": "not-an-id"}) + "\n")
    assert trust.events(project_dir=project) == []


def test_events_skips_a_non_list_evidence_field(project, tmp_checkpoint_dir):
    import json as _json
    from daimon_briefing import config, store
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_json.dumps({"event": "quarantined",
                                  "quarantine_id": "tr-000000000000",
                                  "evidence": "not-a-list"}) + "\n")
    assert trust.events(project_dir=project) == []


def test_fold_drops_a_hand_edited_unknown_kind():
    rows = [{"quarantine_id": "tr-000000000000", "event": "quarantined",
            "channel": "cli-agent", "kind": "not-a-real-kind", "order": 1,
            "event_id": "e1", "_line": 0}]
    assert trust.fold(rows) == {}


def test_fold_ignores_an_orphan_lifecycle_event():
    rows = [{"quarantine_id": "tr-000000000000", "event": "confirmed",
            "channel": "cli-tty", "order": 1, "event_id": "e1", "_line": 0}]
    assert trust.fold(rows) == {}


def test_fold_ignores_a_lifecycle_event_from_a_non_human_channel():
    rows = [
        {"quarantine_id": "tr-000000000000", "event": "quarantined",
         "channel": "cli-agent", "kind": "decision", "order": 1,
         "event_id": "e1", "_line": 0},
        {"quarantine_id": "tr-000000000000", "event": "confirmed",
         "channel": "cli-agent", "order": 2, "event_id": "e2", "_line": 1},
    ]
    record = trust.fold(rows)["tr-000000000000"]
    assert record["state"] == "candidate"


def test_propose_rejects_a_malformed_item_id(project):
    with pytest.raises(trust.TrustError):
        _propose(project, item_id="not-a-valid-item-id")


# ---- forget_content_key: internal branches -----------------------------


def test_forget_content_key_missing_ledger_returns_empty(project):
    from daimon_briefing import normalize
    assert trust.forget_content_key(
        normalize.content_key("anything"), project_dir=project) == []


def test_forget_content_key_unresolvable_project_returns_empty(monkeypatch):
    from daimon_briefing import store
    monkeypatch.setattr(store, "project_slug", lambda p: None)
    assert trust.forget_content_key("deadbeef", project_dir="/p/x") == []


def test_forget_content_key_unreadable_ledger_returns_empty(
        project, tmp_checkpoint_dir, monkeypatch):
    from pathlib import Path
    _propose(project)
    monkeypatch.setattr(
        Path, "read_text",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert trust.forget_content_key("deadbeef", project_dir=project) == []


def test_forget_content_key_skips_malformed_scan_lines(
        project, tmp_checkpoint_dir):
    from daimon_briefing import config, normalize, store
    _propose(project, reason="the specific reason text to forget")
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write("[1, 2]\n")
        handle.write("\n")
    key = normalize.content_key("the specific reason text to forget")
    removed = trust.forget_content_key(key, project_dir=project)
    assert len(removed) == 1


def test_forget_content_key_keeps_the_other_record(project, tmp_checkpoint_dir):
    """A ledger with TWO records, only one matched: the survivor's line must
    be written back (the `kept.append(line)` branch)."""
    from daimon_briefing import normalize
    kept_reason = "a totally unrelated survivor reason text"
    doomed_tid = _propose(project, reason="the doomed reason text right here")
    _propose(project, text="a second, unrelated quarantined value here",
             reason=kept_reason)
    key = normalize.content_key("the doomed reason text right here")
    removed = trust.forget_content_key(key, project_dir=project)
    assert removed == [doomed_tid]
    remaining = trust.records(project_dir=project)
    assert doomed_tid not in remaining
    assert any(r["reason"] == kept_reason for r in remaining.values())


def test_forget_content_key_atomic_write_failure_returns_empty(
        project, tmp_checkpoint_dir, monkeypatch):
    import os as _os
    _propose(project, reason="a very specific reason to try to forget")
    from daimon_briefing import normalize
    key = normalize.content_key("a very specific reason to try to forget")
    monkeypatch.setattr(
        _os, "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert trust.forget_content_key(key, project_dir=project) == []


def test_forget_content_key_cleanup_unlink_also_fails(
        project, tmp_checkpoint_dir, monkeypatch):
    """Both the replace AND the tmp-file cleanup fail: the second `except
    OSError: pass` must swallow it too, still returning []."""
    import os as _os
    from pathlib import Path
    _propose(project, reason="a very specific reason to try to forget too")
    from daimon_briefing import normalize
    key = normalize.content_key("a very specific reason to try to forget too")
    monkeypatch.setattr(
        _os, "replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(
        Path, "unlink",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom too")))
    assert trust.forget_content_key(key, project_dir=project) == []


# ---- append/events/fold: remaining defensive branches ----------------------


def test_append_repairs_a_torn_ledger(project, tmp_checkpoint_dir):
    from daimon_briefing import config, store
    _propose(project)
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    with path.open("r+b") as handle:
        handle.seek(-1, 2)
        handle.truncate()  # drop the trailing newline: now torn
    _propose(project, text="a second, distinct quarantined value entirely",
             reason="a second reason")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the repair inserted the missing newline first


def test_events_returns_empty_for_an_unresolvable_project(monkeypatch):
    from daimon_briefing import store
    monkeypatch.setattr(store, "project_slug", lambda p: None)
    assert trust.events(project_dir="/p/x") == []


def test_events_returns_empty_on_a_read_oserror(project, tmp_checkpoint_dir,
                                                monkeypatch):
    from pathlib import Path
    _propose(project)

    def boom(self, *a, **k):
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", boom)
    assert trust.events(project_dir=project) == []


def test_fold_tolerates_a_non_integer_order():
    rows = [{"quarantine_id": "tr-000000000000", "event": "quarantined",
            "channel": "cli-agent", "kind": "decision", "order": "not-a-number",
            "event_id": "e1", "_line": 0}]
    record = trust.fold(rows)["tr-000000000000"]
    assert record["state"] == "candidate"


def test_fold_first_writer_wins_on_a_hand_duplicated_proposal():
    """Two `quarantined` rows for the same id while the record is still a
    live candidate: the second is a duplicate, first writer wins — the
    normal write path (`propose`) refuses this before it ever reaches the
    ledger, so only a hand-edited or malformed ledger produces it."""
    rows = [
        {"quarantine_id": "tr-000000000000", "event": "quarantined",
         "channel": "cli-agent", "kind": "decision", "reason": "first",
         "order": 1, "event_id": "e1", "_line": 0},
        {"quarantine_id": "tr-000000000000", "event": "quarantined",
         "channel": "cli-agent", "kind": "decision", "reason": "second",
         "order": 2, "event_id": "e2", "_line": 1},
    ]
    record = trust.fold(rows)["tr-000000000000"]
    assert record["reason"] == "first"


def test_human_transition_reports_a_failed_append(project, monkeypatch):
    tid = _propose(project)
    monkeypatch.setattr(trust, "append", lambda *a, **k: False)
    with pytest.raises(trust.TrustError, match="not written"):
        trust.confirm(tid, channel="cli-tty", project_dir=project)


# ---- privacy audit reachability --------------------------------------------
#
# #645's own history: a plaintext ledger declared in SURFACES but not wired
# into privacy.audit_project's scan lands in `unknown`, then `unscannable`,
# pinning every audit of the project at exit 3 from the first write onward.
# These pin that trust.jsonl cannot repeat that arc.


def test_privacy_audit_does_not_flag_trust_ledger_as_unscannable(project):
    from daimon_briefing import privacy
    _propose(project)
    result = privacy.audit_project(project_dir=project)
    assert result["unscannable"] == []
    assert result["trust"]["records"] == 1
    assert result["trust"]["rows"] == 1
    assert result["trust"]["bytes"] > 0


def test_render_privacy_audit_prints_the_trust_ledger_line(project, capsys):
    """The "growth measured, never silent" line every other plaintext ledger
    here gets (`render.render_privacy_audit`'s amendment/request lines) —
    covers the TRUE branch `test_render_covers_every_line_shape` in
    test_audit_privacy.py does not exercise for any ledger."""
    from daimon_briefing import cli, store

    # A live checkpoint, so the audit does not report zero_surfaces (rc 3):
    # what is under test here is the trust-ledger render line, not the
    # zero-surfaces posture.
    store.write_checkpoint("S-1", {"session_id": "S-1"}, project_dir=project)
    _propose(project)
    rc = cli.main(["audit", "privacy", "--project", project])
    out = capsys.readouterr().out
    assert rc == 0
    assert "trust ledger: 1 record(s) in 1 row(s)" in out
    assert "nothing reads this ledger yet" in out


def test_privacy_audit_skips_malformed_trust_lines(project, tmp_checkpoint_dir):
    """A torn/malformed line or a non-dict JSON value in trust.jsonl must not
    sink the scan — mirrors the identical defensive read every other ledger
    block in `audit_project` holds."""
    from daimon_briefing import config, privacy, store

    _propose(project)
    slug = store.project_slug(project)
    path = config.checkpoint_dir() / slug / "trust.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write("[1, 2, 3]\n")

    result = privacy.audit_project(project_dir=project)
    assert result["unscannable"] == []
    assert result["trust"]["rows"] == 1  # the two bad lines are not counted


def test_privacy_audit_finds_forgotten_trust_reason(project):
    from daimon_briefing import normalize, privacy, store

    reason_text = "a very specific fabricated claim, forgotten later"
    _propose(project, reason=reason_text)
    key = normalize.content_key(reason_text)
    store.append_event("x-irrelevant", f"forgotten:{key}",
                       project_dir=project)

    result = privacy.audit_project(project_dir=project)
    hits = [f for f in result["findings"] if f["surface"] == "trust-ledger"]
    assert len(hits) == 1
    assert hits[0]["content_hash"] == key
