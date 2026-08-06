"""#599: forget must reach quote/scene item fields and the event ledger.

Every forget path keyed on `normalize.content_key(item["text"])` alone, so a
forgotten value survived deletion wherever it sat in another item's `quote` or
`scene` field, and in `events.jsonl` `item_text` / free-form `status` / `note`
rows written before the forget. The contract (#321/#419): holding plaintext is
what puts a file inside the deletion contract, not its role.

Field scrub semantics (not whole-item drop): an item whose TEXT folds to the
tombstoned key is removed entirely (unchanged behavior); an item that merely
carries the value in `quote`/`scene` loses that field. A scrubbed quote also
loses its verification claim — `quote_provenance` / `quote_verified` /
`last_verified` / `source_message_ids` go with it and `trust` drops to
"inferred" — because a stale receipt would keep corroboration treating the
item as capture-verified, and `trust=verbatim` with no quote fails
serializer revalidation.

Event rows are never dropped (scar 0025: the resolutions fold keys on
`item_ref` and row order); the matching FIELD value is replaced in place with
`[forgotten:<content_key>]` — visible, auditable, and impossible to collide
with the value it names.
"""
import json

from daimon_briefing import cli, normalize, privacy, recall, store

PROJECT = "/p/forget-field-residue"
CANARY = "zqxfieldcanary9917 the staging db password rotates on fridays"
KEEPER = "an unrelated decision that must survive the forget"
FRESH = "a decision captured after the forget happened"


def _checkpoint(sid, created, items):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {"recent_decisions": items},
    }


def _write(sid, created, items):
    store.write_checkpoint(sid, _checkpoint(sid, created, items),
                           project_dir=PROJECT)


def _forget_canary():
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0


def _live_decisions():
    cp = store.read_latest(project_dir=PROJECT, fallback=False)
    return (cp.get("working_context") or {}).get("recent_decisions") or []


def _events_rows():
    path = store._events_path(PROJECT)
    return [json.loads(line) for line in path.read_text().splitlines() if line]


KEY = normalize.content_key(CANARY)
MARKER = f"[forgotten:{KEY}]"


# ---- item fields: quote / scene ------------------------------------------


def test_forget_scrubs_quote_copy_and_its_verification_claim(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": CANARY, "trust": "inferred"},
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY,
         "quote_verified": True, "last_verified": "2026-08-01T00:00:00Z",
         "source_message_ids": ["m-1"],
         "quote_provenance": {"version": 1, "outcome": "verified"}},
    ])
    _forget_canary()
    kept = _live_decisions()
    assert [i["text"] for i in kept] == [KEEPER]
    survivor = kept[0]
    assert "quote" not in survivor
    assert survivor["trust"] == "inferred"
    for stale in ("quote_provenance", "quote_verified", "last_verified",
                  "source_message_ids"):
        assert stale not in survivor, f"{stale} must not outlive the quote"


def test_forget_scrubs_scene_copy_without_touching_trust(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": CANARY, "trust": "inferred"},
        {"text": KEEPER, "trust": "inferred", "scene": CANARY},
    ])
    _forget_canary()
    kept = _live_decisions()
    assert [i["text"] for i in kept] == [KEEPER]
    assert "scene" not in kept[0]
    assert kept[0]["trust"] == "inferred"


def test_forget_scrubs_quote_copy_in_prev_history(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": CANARY, "trust": "inferred"},
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY},
    ])
    # rotation: S1 state now sits in prev-1 and in its session file
    _write("S2", "2026-08-02T00:00:00Z", [
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY},
    ])
    _forget_canary()
    residue = [str(p) for p in store.project_surfaces(PROJECT)
               if CANARY in p.read_text()]
    assert residue == [], f"plaintext survives in {residue}"


def test_recapture_with_forgotten_quote_is_scrubbed_at_the_write_gate(
        tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [{"text": CANARY, "trust": "inferred"}])
    _forget_canary()
    # a later session re-extracts the value inside a fresh item's quote
    _write("S2", "2026-08-02T00:00:00Z", [
        {"text": FRESH, "trust": "verbatim", "quote": CANARY},
    ])
    kept = _live_decisions()
    assert [i["text"] for i in kept] == [FRESH]
    assert "quote" not in kept[0]
    assert kept[0]["trust"] == "inferred"


def test_rebuilt_recall_index_holds_no_quote_residue(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": CANARY, "trust": "inferred"},
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY},
    ])
    recall.rebuild()
    _forget_canary()
    recall.rebuild()
    import sqlite3

    from daimon_briefing import config
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = conn.execute(
            "SELECT text, quote, scene FROM items").fetchall()
    finally:
        conn.close()
    assert all(CANARY not in (field or "") for row in rows for field in row)


# ---- the plaintext-bearing CLASS: active_topic + links[].target -----------
# (refuter finding 3: redact_checkpoint's enumeration is the class — item
# text/quote/scene, links[].target, and the active_topic singleton — and a
# fix that patches two named fields repeats the #575 class mistake.)


def test_forget_drops_active_topic_carrying_the_value(tmp_checkpoint_dir):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": CANARY, "trust": "inferred"},
            "recent_decisions": [{"text": CANARY, "trust": "inferred"},
                                 {"text": KEEPER, "trust": "inferred"}]},
    }, project_dir=PROJECT)
    _forget_canary()
    cp = store.read_latest(project_dir=PROJECT, fallback=False)
    assert "active_topic" not in (cp.get("working_context") or {})
    residue = [str(p) for p in store.project_surfaces(PROJECT)
               if CANARY in p.read_text()]
    assert residue == [], f"plaintext survives in {residue}"


def test_forget_scrubs_active_topic_quote(tmp_checkpoint_dir):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": KEEPER, "quote": CANARY,
                             "trust": "verbatim"},
            "recent_decisions": [{"text": CANARY, "trust": "inferred"}]},
    }, project_dir=PROJECT)
    _forget_canary()
    cp = store.read_latest(project_dir=PROJECT, fallback=False)
    topic = (cp.get("working_context") or {}).get("active_topic")
    assert topic and topic["text"] == KEEPER
    assert "quote" not in topic
    assert topic["trust"] == "inferred"


def test_forget_drops_link_whose_target_is_the_value(tmp_checkpoint_dir):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"},
            {"text": KEEPER, "trust": "inferred",
             "links": [{"type": "supersedes", "target": CANARY},
                       {"type": "supersedes", "target": "r-abcdef123456"}]},
        ]},
    }, project_dir=PROJECT)
    _forget_canary()
    kept = _live_decisions()
    assert [i["text"] for i in kept] == [KEEPER]
    links = kept[0].get("links")
    assert links == [{"type": "supersedes", "target": "r-abcdef123456"}], \
        f"only the forgotten-target link goes: {links}"
    residue = [str(p) for p in store.project_surfaces(PROJECT)
               if CANARY in p.read_text()]
    assert residue == [], f"plaintext survives in {residue}"


# ---- event ledger: item_text / status / note -----------------------------


def test_forget_scrubs_event_item_text_in_place(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [{"text": CANARY, "trust": "inferred"}])
    store.append_event("i-y", "resolved", item_text=CANARY,
                       project_dir=PROJECT)
    _forget_canary()
    rows = _events_rows()
    scrubbed = [r for r in rows if r.get("item_ref") == "i-y"]
    assert scrubbed and scrubbed[0]["item_text"] == MARKER
    # row structure survives: same keys, untouched fields byte-identical
    assert scrubbed[0]["status"] == "resolved"
    assert set(scrubbed[0]) >= {"ts", "kind", "item_ref", "status", "item_text"}


def test_forget_scrubs_event_status_and_note_fields(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [{"text": CANARY, "trust": "inferred"}])
    store.append_event("i-y", CANARY, project_dir=PROJECT)
    store.append_event("i-z", "resolved", note=CANARY, project_dir=PROJECT)
    _forget_canary()
    rows = _events_rows()
    by_ref = {r["item_ref"]: r for r in rows if r.get("item_ref") in ("i-y", "i-z")}
    assert by_ref["i-y"]["status"] == MARKER
    assert by_ref["i-z"]["note"] == MARKER
    assert by_ref["i-z"]["status"] == "resolved"


def test_events_scrub_preserves_unrelated_rows_verbatim(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [{"text": CANARY, "trust": "inferred"}])
    store.append_event("i-k", "resolved", item_text=KEEPER, note="fine",
                       project_dir=PROJECT)
    before = [r for r in _events_rows() if r.get("item_ref") == "i-k"]
    _forget_canary()
    after = [r for r in _events_rows() if r.get("item_ref") == "i-k"]
    assert after == before


def test_status_scrub_preserves_reopen_classification(tmp_checkpoint_dir):
    """Refuter finding 1 (BLOCKER): every status reader classifies by PREFIX
    (is_resolved, _tie_rank, _demotes, the recall fold). A revival whose
    free-form status happens to BE the forgotten sentence must stay a
    revival after the scrub — a bare marker re-classified it as free-form
    resolved and hid the unrelated item forever."""
    revival = "reopen the postgres migration debate before shipping"
    rkey = normalize.content_key(revival)
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": "b-victim item that must stay live", "trust": "inferred"},
        {"text": revival, "trust": "inferred"},
    ])
    victim_id = next(i["id"] for i in _live_decisions()
                     if i["text"].startswith("b-victim"))
    store.append_event(victim_id, "resolved", project_dir=PROJECT)
    store.append_event(victim_id, revival, project_dir=PROJECT)  # revived
    assert not store.is_resolved(store.resolutions(project_dir=PROJECT)[victim_id])
    assert cli.main(["forget", revival, "--project", PROJECT]) == 0
    evt = store.resolutions(project_dir=PROJECT)[victim_id]
    assert not store.is_resolved(evt), \
        f"revival lost: victim folded to {evt.get('status')!r}"
    assert evt["status"] == f"reopen [forgotten:{rkey}]"


def test_status_scrub_keeps_free_form_resolved_class(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": "some other item", "trust": "inferred"},
        {"text": CANARY, "trust": "inferred"},
    ])
    other_id = next(i["id"] for i in _live_decisions()
                    if i["text"] == "some other item")
    store.append_event(other_id, CANARY, project_dir=PROJECT)
    assert store.is_resolved(store.resolutions(project_dir=PROJECT)[other_id])
    _forget_canary()
    evt = store.resolutions(project_dir=PROJECT)[other_id]
    assert store.is_resolved(evt)
    assert evt["status"] == MARKER


def test_event_scrub_routes_rewritten_rows_through_admit_row(
        tmp_checkpoint_dir, monkeypatch):
    """Refuter finding 4: the ledger rewrite must be GOVERNED, not merely
    read as governed via the guard's correlation blind spot. Every rewritten
    row passes through policy.admit_row — the same admission seam the append
    took — so the write-audit architecture guard can correlate the row that
    landed on disk with a real admission."""
    from daimon_briefing import policy
    _write("S1", "2026-08-01T00:00:00Z", [{"text": CANARY, "trust": "inferred"}])
    store.append_event("i-y", "resolved", item_text=CANARY,
                       project_dir=PROJECT)
    admitted = []
    orig = policy.admit_row

    def spy(row, redact_fields=(), redact_fn=None):
        admitted.append(dict(row))
        return orig(row, redact_fields=redact_fields, redact_fn=redact_fn)

    monkeypatch.setattr(policy, "admit_row", spy)
    assert store.scrub_event_fields(KEY, project_dir=PROJECT) == 1
    assert any(r.get("item_text") == MARKER for r in admitted), \
        "the rewritten row never passed the admission seam"


# ---- the detector agrees the value is gone --------------------------------


def test_audit_privacy_is_clean_after_forget(tmp_checkpoint_dir):
    _write("S1", "2026-08-01T00:00:00Z", [
        {"text": CANARY, "trust": "inferred"},
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY,
         "quote_verified": True},
        {"text": FRESH, "trust": "inferred", "scene": CANARY},
    ])
    store.append_event("i-y", "resolved", item_text=CANARY,
                       project_dir=PROJECT)
    _write("S2", "2026-08-02T00:00:00Z", [
        {"text": KEEPER, "trust": "verbatim", "quote": CANARY},
    ])
    _forget_canary()
    result = privacy.audit_project(project_dir=PROJECT)
    hits = [f for f in result["findings"] if f["content_hash"] == KEY]
    assert hits == [], f"audit still finds residue: {hits}"
