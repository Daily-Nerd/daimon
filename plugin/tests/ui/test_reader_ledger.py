"""project_ledger: the all-objects walk behind the ledger screen (#670 slice 2).

Grouping rule under test: an object sits under the session of its latest
recorded transition (first seen / changed / last seen), never under a carry —
agreement is not corroboration, and a carried rung is not an event. The LAST
EVENT column may still be later than the group when a resolution or quote
check (which carry their own ts but no session attribution) postdates the walk.
"""
import json
import pytest
from daimon_ui import reader

def _cp(sid, created, slug, items, topic="t"):
    return {
        "session_id": sid, "format_version": "D-019", "created": created,
        "author": "ada", "project_slug": slug,
        "working_context": {"active_topic": {"text": topic},
                            "open_questions": items, "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }

def _item(iid, text, trust=None):
    it = {"id": iid, "text": text}
    if trust:
        it["trust"] = trust
    return it

@pytest.fixture
def ledger_history(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    # s1: o-aaa111aaa111 and o-bbb222bbb222 first seen
    (d / "s1-aaaa.json").write_text(json.dumps(_cp(
        "s1-aaaa", "2026-08-04T10:00:00Z", slug,
        [_item("o-aaa111aaa111", "build gates on bundle", "inferred"),
         _item("o-bbb222bbb222", "old question")])))
    # s2: o-aaa changed (trust), o-bbb carried, o-ccc first seen
    (d / "s2-bbbb.json").write_text(json.dumps(_cp(
        "s2-bbbb", "2026-08-05T10:00:00Z", slug,
        [_item("o-aaa111aaa111", "build gates on bundle", "verbatim"),
         _item("o-bbb222bbb222", "old question"),
         _item("o-ccc333ccc333", "token lifetime rewritten", "verbatim")])))
    # s3 (head): o-aaa carried, o-ccc carried, o-bbb gone (last seen), o-ddd first seen
    (d / "s3-cccc.json").write_text(json.dumps(_cp(
        "s3-cccc", "2026-08-06T10:00:00Z", slug,
        [_item("o-aaa111aaa111", "build gates on bundle", "verbatim"),
         _item("o-ccc333ccc333", "token lifetime rewritten", "verbatim"),
         _item("o-ddd444ddd444", "database client rule")])))
    (d / slug / "latest.json").write_text(json.dumps(_cp(
        "s3-cccc", "2026-08-06T10:00:00Z", slug, [])))
    # o-bbb was resolved after vanishing; o-aaa got a later quote check
    (d / slug / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-08-06T11:00:00Z", "kind": "resolution", "status": "resolved",
         "item_ref": "o-bbb222bbb222", "note": "closed it", "source": "cli"}) + "\n")
    (d / slug / "verification.jsonl").write_text(json.dumps(
        {"ts": "2026-08-06T12:00:00Z", "check": "quote", "reason": "compacted",
         "item_ref": "o-aaa111aaa111"}) + "\n")
    return d, slug

def _group(result, sid):
    hits = [g for g in result["groups"] if g["session_id"] == sid]
    assert hits, f"no group for {sid}: {[g['session_id'] for g in result['groups']]}"
    return hits[0]

def _row(group, iid):
    hits = [r for r in group["rows"] if r["id"] == iid]
    assert hits, f"no row {iid} in group {group['session_id']}"
    return hits[0]

def test_groups_are_newest_first_and_carry_counts(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    assert result["ok"] is True
    assert [g["session_id"] for g in result["groups"]] == ["s3-cccc", "s2-bbbb"]
    assert _group(result, "s3-cccc")["counts"] == {"first_seen": 1, "changed": 0, "last_seen": 1}
    assert _group(result, "s2-bbbb")["counts"] == {"first_seen": 1, "changed": 1, "last_seen": 0}
    # s1 keeps nothing: both its objects moved on (o-aaa changed at s2,
    # o-bbb last seen at s3) — but the group only exists when it has rows.
    assert not any(g["session_id"] == "s1-aaaa" for g in result["groups"])

def test_object_rows_sit_under_their_latest_transition(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    s3 = _group(result, "s3-cccc")
    assert {r["id"] for r in s3["rows"]} == {"o-ddd444ddd444", "o-bbb222bbb222"}
    s2 = _group(result, "s2-bbbb")
    assert {r["id"] for r in s2["rows"]} == {"o-aaa111aaa111", "o-ccc333ccc333"}

def test_last_event_prefers_later_resolution_and_quote_check(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    # o-bbb vanished at the s3 transition but was then resolved: resolution wins.
    bbb = _row(_group(result, "s3-cccc"), "o-bbb222bbb222")
    assert bbb["last_event"]["kind"] == "resolved"
    assert bbb["last_event"]["ts"] == "2026-08-06T11:00:00Z"
    # o-aaa changed at s2 but a later quote check postdates it.
    aaa = _row(_group(result, "s2-bbbb"), "o-aaa111aaa111")
    assert aaa["last_event"]["kind"] == "quote_check"
    assert aaa["last_event"]["ts"] == "2026-08-06T12:00:00Z"
    # untouched-after-birth object keeps its first-seen event.
    ddd = _row(_group(result, "s3-cccc"), "o-ddd444ddd444")
    assert ddd["last_event"]["kind"] == "first_seen"
    assert ddd["last_event"]["ts"] == "2026-08-06T10:00:00Z"

def test_last_seen_event_names_the_last_sighting_not_the_transition(ledger_history):
    d, slug = ledger_history
    # o-bbb without the resolution: last_seen ts = created of its final sighting (s2).
    (d / slug / "events.jsonl").write_text("")
    result = reader.project_ledger(d, slug)
    bbb = _row(_group(result, "s3-cccc"), "o-bbb222bbb222")
    assert bbb["last_event"]["kind"] == "last_seen"
    assert bbb["last_event"]["ts"] == "2026-08-05T10:00:00Z"

def test_totals_count_objects_and_recorded_events(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    assert result["totals"]["objects"] == 4
    # 2 born@s1 + 1 born@s2 + 1 changed@s2 + 1 born@s3 + 1 last_seen@s3
    # + 1 resolution + 1 quote check
    assert result["totals"]["events"] == 8

def test_head_is_the_newest_session(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    assert result["head"]["session_id"] == "s3-cccc"
    assert result["head"]["created"] == "2026-08-06T10:00:00Z"

def test_rows_carry_text_and_trust_for_rendering(ledger_history):
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    aaa = _row(_group(result, "s2-bbbb"), "o-aaa111aaa111")
    assert aaa["text"] == "build gates on bundle"
    assert aaa["trust"] == "verbatim"

def test_rows_carry_the_recall_kind_word(ledger_history):
    """The chip prints recall's vocabulary — question/decision/belief/
    uncertainty/contradiction — never a viewer-coined section name. Fixture
    items live in open_questions, so every row here is a question."""
    d, slug = ledger_history
    result = reader.project_ledger(d, slug)
    aaa = _row(_group(result, "s2-bbbb"), "o-aaa111aaa111")
    assert aaa["kind"] == "question"

def test_empty_project_is_ok_and_empty(tmp_path):
    d = tmp_path / "checkpoints"
    d.mkdir()
    result = reader.project_ledger(d, "-nope")
    assert result == {"ok": True, "groups": [], "head": None,
                      "totals": {"objects": 0, "events": 0}, "partial": []}

def test_unreadable_session_files_land_in_partial(ledger_history):
    d, slug = ledger_history
    (d / "torn.json").write_text("{nope")
    result = reader.project_ledger(d, slug)
    assert result["ok"] is True
    assert any("read" in p for p in result["partial"])

def test_walk_skips_a_session_that_tears_between_listing_and_reading(ledger_history, monkeypatch):
    """project_history lists from filenames; a file torn (or deleted) between
    that listing and the walk's read must be skipped, not abort the ledger."""
    d, slug = ledger_history
    real = reader._load_session

    def flaky(data_dir, sid):
        if sid == "s2-bbbb":
            return None, {"what": "torn", "why": "torn", "fix": "heal"}
        return real(data_dir, sid)

    monkeypatch.setattr(reader, "_load_session", flaky)
    result = reader.project_ledger(d, slug)
    assert result["ok"] is True
    # s2 never scanned: o-ccc is first seen at s3 instead, and no changed
    # event for o-aaa exists anywhere.
    ids = {r["id"] for g in result["groups"] for r in g["rows"]}
    assert "o-ccc333ccc333" in ids
