import json
from daimon_ui import reader
from tests.ui.conftest import make_checkpoint

BIO_ID = "o-abc123abc123"
LATE_BORN_ID = "o-def456def456"


def _write_bio_sessions(d, slug):
    # aaaa-1111 (day one, oldest): item is born here, verbatim, with a quote.
    cp_a = make_checkpoint(
        created="2026-08-04T10:00:00Z", topic="day one", session_id="aaaa-1111",
        open_questions=[
            {"text": "root cause of the outage", "id": BIO_ID, "trust": "verbatim",
             "quote": "it was the DNS all along", "quote_verified": True},
        ],
    )
    cp_a["project_slug"] = slug
    (d / "aaaa-1111.json").write_text(json.dumps(cp_a))

    # bbbb-2222 (day two): item carried unchanged -> "seen". Also where LATE_BORN_ID appears
    # for the first time (not the oldest session) so window_note should NOT fire for it.
    cp_b = make_checkpoint(
        created="2026-08-05T10:00:00Z", topic="day two", session_id="bbbb-2222",
        open_questions=[
            {"text": "root cause of the outage", "id": BIO_ID, "trust": "verbatim",
             "quote": "it was the DNS all along", "quote_verified": True},
            {"text": "second question", "id": LATE_BORN_ID, "trust": "inferred"},
        ],
    )
    cp_b["project_slug"] = slug
    (d / "bbbb-2222.json").write_text(json.dumps(cp_b))

    # rollout-2026-08-06-cccc (day three, newest): trust mutated verbatim -> inferred.
    cp_c = make_checkpoint(
        created="2026-08-06T10:00:00Z", topic="day three", session_id="rollout-2026-08-06-cccc",
        open_questions=[
            {"text": "root cause of the outage", "id": BIO_ID, "trust": "inferred",
             "quote": "it was the DNS all along", "quote_verified": True},
            {"text": "second question", "id": LATE_BORN_ID, "trust": "inferred"},
        ],
    )
    cp_c["project_slug"] = slug
    (d / "rollout-2026-08-06-cccc.json").write_text(json.dumps(cp_c))


def test_item_biography_rejects_path_traversal_id(flat_history):
    d, slug = flat_history
    got = reader.item_biography(d, slug, "../x")
    assert got["ok"] is False
    assert set(got["error"]) == {"what", "why", "fix"}


def test_item_biography_rejects_malformed_id(flat_history):
    d, slug = flat_history
    got = reader.item_biography(d, slug, "o-XYZ")
    assert got["ok"] is False
    assert set(got["error"]) == {"what", "why", "fix"}


def test_item_biography_unknown_valid_id_points_at_diff_view(flat_history):
    d, slug = flat_history
    got = reader.item_biography(d, slug, "o-ffffff000000")
    assert got["ok"] is False
    assert "diff" in got["error"]["fix"].lower()


def test_item_biography_born_carried_changed_sequence(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)

    got = reader.item_biography(d, slug, BIO_ID)
    assert got["ok"] is True

    kinds = [e["kind"] for e in got["events"]]
    assert kinds == ["born", "seen", "changed"]

    born = got["events"][0]
    assert born["session_id"] == "aaaa-1111"
    assert born["ts_or_created"] == "2026-08-04T10:00:00Z"
    assert born["detail"] == "it was the DNS all along"

    seen = got["events"][1]
    assert seen["session_id"] == "bbbb-2222"
    assert seen["detail"] is None

    changed = got["events"][2]
    assert changed["session_id"] == "rollout-2026-08-06-cccc"
    assert "trust" in changed["detail"]

    assert got["item"]["id"] == BIO_ID
    assert got["item"]["trust"] == "inferred"  # latest sighting
    assert got["item"]["section"] == "open_loops"

    # item already present in the oldest scanned session -> window_note fires.
    assert got["window_note"] == "history starts here — earlier sessions may have been cleaned up"


def test_item_biography_no_window_note_when_born_after_oldest_session(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)

    got = reader.item_biography(d, slug, LATE_BORN_ID)
    assert got["ok"] is True
    assert got["events"][0]["kind"] == "born"
    assert got["events"][0]["session_id"] == "bbbb-2222"
    assert got["window_note"] is None


def test_item_biography_verified_event_folded_in_ts_order(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    bucket = d / slug
    bucket.mkdir(exist_ok=True)
    (bucket / "verification.jsonl").write_text('\n'.join([
        json.dumps({"ts": "2026-08-05T15:00:00Z", "check": "quote", "item_ref": BIO_ID,
                     "reason": "quote-not-in-transcript"}),
        '{broken line',
        json.dumps({"ts": "2026-08-05T16:00:00Z", "check": "quote", "item_ref": "o-someone-else",
                     "reason": "quote-not-in-transcript"}),
    ]) + '\n')

    got = reader.item_biography(d, slug, BIO_ID)
    assert got["ok"] is True
    kinds = [e["kind"] for e in got["events"]]
    # verified sits between "seen" (day two, 08-05T10:00) and "changed" (day three, 08-06T10:00)
    assert kinds == ["born", "seen", "verified", "changed"]
    verified = got["events"][2]
    assert verified["session_id"] is None
    assert verified["ts_or_created"] == "2026-08-05T15:00:00Z"
    assert "quote-not-in-transcript" in verified["detail"]


def test_item_biography_resolved_event_from_bucket_events(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    bucket = d / slug
    bucket.mkdir(exist_ok=True)
    (bucket / "events.jsonl").write_text(json.dumps({
        "ts": "2026-08-07T09:00:00Z", "kind": "resolution", "item_ref": BIO_ID,
        "status": "resolved", "note": "root cause confirmed and fixed",
    }) + '\n')

    got = reader.item_biography(d, slug, BIO_ID)
    assert got["ok"] is True
    assert got["events"][-1]["kind"] == "resolved"
    assert got["events"][-1]["ts_or_created"] == "2026-08-07T09:00:00Z"
    assert got["events"][-1]["detail"] == "root cause confirmed and fixed"
    assert got["events"][-1]["session_id"] is None


def test_biography_includes_trust_anatomy_block(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["ok"] is True
    a = got["trust_anatomy"]
    assert a["stored"]["trust"] == "inferred"          # newest sighting wins
    assert a["stored"]["quote"] == "it was the DNS all along"
    assert a["stored"]["quote_verified"] is True
    assert [c["session_id"] for c in a["chain"]] == [
        "aaaa-1111", "bbbb-2222", "rollout-2026-08-06-cccc"]
    assert a["chain"][0]["changed"] == []
    assert a["chain"][2]["changed"] == ["trust"]
    assert a["checks"]["quote_check_failures"] == 0
    assert a["checks"]["last_check_ts"] is None


def test_biography_origin_on_disk_true_when_first_sighting_file_exists(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    got = reader.item_biography(d, slug, BIO_ID)
    # no origin_session stored -> falls back to first-sighting session aaaa-1111,
    # whose file exists in the flat dir
    assert got["trust_anatomy"]["checks"]["origin_on_disk"] is True


def test_biography_origin_on_disk_false_when_stored_origin_missing(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    cp = json.loads((d / "rollout-2026-08-06-cccc.json").read_text())
    cp["working_context"]["open_questions"][0]["origin_session"] = "gone-0000"
    (d / "rollout-2026-08-06-cccc.json").write_text(json.dumps(cp))
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["trust_anatomy"]["checks"]["origin_on_disk"] is False


def test_biography_origin_on_disk_false_for_path_traversal_origin_session(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    # a file that exists OUTSIDE the checkpoints dir -- if origin_session were used
    # unvalidated in path construction, "../secret" would resolve to it and turn
    # origin_on_disk into an existence oracle over arbitrary .json paths.
    (d.parent / "secret.json").write_text("{}")
    cp = json.loads((d / "rollout-2026-08-06-cccc.json").read_text())
    cp["working_context"]["open_questions"][0]["origin_session"] = "../secret"
    (d / "rollout-2026-08-06-cccc.json").write_text(json.dumps(cp))
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["ok"] is True
    assert got["trust_anatomy"]["checks"]["origin_on_disk"] is False


def test_biography_receipt_from_quote_provenance(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    cp = json.loads((d / "rollout-2026-08-06-cccc.json").read_text())
    cp["working_context"]["open_questions"][0]["quote_provenance"] = {
        "verifier": "quote-v2", "outcome": "verified",
        "checked_at": "2026-08-06T10:00:00Z",
        "digest": {"algorithm": "sha256", "value": "ff"},
        "binding": {"message_ids": ["m1", "m2"]},
    }
    (d / "rollout-2026-08-06-cccc.json").write_text(json.dumps(cp))
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["trust_anatomy"]["receipt"]["verifier"] == "quote-v2"
    assert got["trust_anatomy"]["receipt"]["message_ids"] == ["m1", "m2"]


def test_biography_receipt_none_when_absent(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["trust_anatomy"]["receipt"] is None


def test_biography_quote_check_failures_counted_with_reason_filter(flat_history):
    d, slug = flat_history
    _write_bio_sessions(d, slug)
    (d / slug).mkdir(exist_ok=True)
    (d / slug / "verification.jsonl").write_text("\n".join([
        json.dumps({"ts": "2026-08-06T09:00:00Z", "check": "quote",
                    "item_ref": BIO_ID, "reason": "quote-not-in-transcript"}),
        json.dumps({"ts": "2026-08-06T11:00:00Z", "check": "quote",
                    "item_ref": BIO_ID, "reason": "quote-not-in-transcript"}),
        json.dumps({"ts": "2026-08-06T12:00:00Z", "check": "quote",
                    "item_ref": BIO_ID}),                       # no reason: not a failure
        json.dumps({"ts": "2026-08-06T13:00:00Z", "check": "quote",
                    "item_ref": "o-other0other0", "reason": "x"}),  # other item
    ]) + "\n")
    got = reader.item_biography(d, slug, BIO_ID)
    assert got["trust_anatomy"]["checks"]["quote_check_failures"] == 2
    assert got["trust_anatomy"]["checks"]["last_check_ts"] == "2026-08-06T11:00:00Z"


def test_biography_single_session_chain_length_one(flat_history):
    d, slug = flat_history
    cp = make_checkpoint(created="2026-08-04T10:00:00Z", topic="solo",
                         session_id="aaaa-1111",
                         open_questions=[{"text": "only", "id": "o-111111111111",
                                          "trust": "verbatim"}])
    cp["project_slug"] = slug
    (d / "aaaa-1111.json").write_text(json.dumps(cp))
    got = reader.item_biography(d, slug, "o-111111111111")
    assert got["ok"] is True
    assert len(got["trust_anatomy"]["chain"]) == 1
