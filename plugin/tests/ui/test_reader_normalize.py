import json
from daimon_ui import reader
from tests.ui.conftest import make_checkpoint

def _write(bucket, name, data):
    (bucket / name).write_text(json.dumps(data) if not isinstance(data, str) else data)

def test_normalizes_sections_and_trust(bucket):
    cp = make_checkpoint(
        open_questions=[
            {"text": "verify me", "trust": "verbatim", "quote": "q1", "id": "o-abc123abc123", "external_state": True},
            {"text": "open loop", "trust": "inferred", "id": "o-def456def456"},
            {"text": "no trust recorded"},
        ],
        recent_decisions=[{"text": "chose B", "trust": "verbatim", "quote": "I like B", "because": "both audiences", "id": "r-aaa111aaa111", "carried_from": "prev-sid"}],
    )
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["ok"] is True
    keys = [s["key"] for s in got["sections"]]
    assert keys == ["verify_first", "decisions", "open_loops", "beliefs", "uncertainties", "contradictions"]
    verify = got["sections"][0]["items"]
    assert [i["text"] for i in verify] == ["verify me"]
    loops = got["sections"][2]["items"]
    assert [i["trust"] for i in loops] == ["inferred", None]
    dec = got["sections"][1]["items"][0]
    assert dec["because"] == "both audiences" and dec["carried_from"] == "prev-sid"

def test_bare_string_contradictions(bucket):
    _write(bucket, "latest.json", make_checkpoint(contradictions_flagged=["raw string item"]))
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    contra = got["sections"][5]["items"]
    assert contra[0]["text"] == "raw string item" and contra[0]["trust"] is None

def test_unknown_format_version_is_partial(bucket):
    _write(bucket, "latest.json", make_checkpoint(format_version="D-099"))
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["ok"] is True and any("D-099" in p for p in got["partial"])

def test_invalid_json_is_error(bucket):
    _write(bucket, "latest.json", "{torn")
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["ok"] is False
    err = got["error"]
    assert set(err) == {"what", "why", "fix"} and "daimon heal" in err["fix"]

def test_bad_ref_rejected(bucket):
    got = reader.load_checkpoint(bucket.parent, bucket.name, "../../../etc/passwd")
    assert got["ok"] is False

def test_worker_queue_never_leaks(bucket):
    _write(bucket, "latest.json", make_checkpoint())
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert "x" not in texts

def test_malformed_section_shape_drift(bucket):
    _write(bucket, "latest.json", make_checkpoint(strong_beliefs="not-a-list"))
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["ok"] is True
    beliefs = got["sections"][3]["items"]
    assert beliefs == []
    assert any("strong_beliefs" in p for p in got["partial"])

def test_missing_pointer_in_chain_is_not_found(bucket):
    got = reader.load_checkpoint(bucket.parent, bucket.name, "prev-9")
    assert got["ok"] is False
    err = got["error"]
    assert set(err) == {"what", "why", "fix"}
    assert "pointer chain" in err["why"].lower()

def test_importance_valid_int_passes_through(bucket):
    cp = make_checkpoint(open_questions=[{"text": "t", "importance": 3, "external_state": True}])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["sections"][0]["items"][0]["importance"] == 3

def test_importance_non_int_string_is_none(bucket):
    cp = make_checkpoint(open_questions=[{"text": "t", "importance": "high", "external_state": True}])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["sections"][0]["items"][0]["importance"] is None

def test_importance_producer_range_passes_through(bucket):
    """The producer scores 1-10 (serializer.py:151, :678). A reader that stops at 5
    deletes the load-bearing half, and the deleted value is indistinguishable from
    honest absence, so nothing raises."""
    cp = make_checkpoint(open_questions=[
        {"text": "seven", "importance": 7, "external_state": True},
        {"text": "ten", "importance": 10, "external_state": True},
    ])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert [i["importance"] for i in got["sections"][0]["items"]] == [7, 10]

def test_importance_out_of_range_is_none(bucket):
    cp = make_checkpoint(open_questions=[{"text": "t", "importance": 11, "external_state": True}])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["sections"][0]["items"][0]["importance"] is None

def test_importance_bool_is_none(bucket):
    cp = make_checkpoint(open_questions=[{"text": "t", "importance": True, "external_state": True}])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["sections"][0]["items"][0]["importance"] is None

def test_importance_missing_is_none(bucket):
    cp = make_checkpoint(open_questions=[{"text": "t", "external_state": True}])
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["sections"][0]["items"][0]["importance"] is None

def test_quote_verified_normalized_to_bool_or_none(bucket):
    cp = make_checkpoint(
        open_questions=[
            {"text": "has true", "quote_verified": True},
            {"text": "has false", "quote_verified": False},
            {"text": "has string", "quote_verified": "yes"},
            {"text": "has number", "quote_verified": 1},
            {"text": "has none", "quote_verified": None},
            {"text": "missing", },
        ],
    )
    _write(bucket, "latest.json", cp)
    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    items = got["sections"][0]["items"] + got["sections"][2]["items"]
    qv_values = [i["quote_verified"] for i in items]
    assert qv_values == [True, False, None, None, None, None]

def test_norm_item_passes_trust_anatomy_fields():
    got = reader._norm_item({
        "text": "x", "id": "o-abc123abc123",
        "last_verified": "2026-08-06T10:00:00Z",
        "origin_session": "aaaa-1111",
        "source_message_ids": ["m1", "m2"],
        "quote_provenance": {
            "verifier": {"id": "tier-f", "version": 1}, "outcome": "verified",
            "checked_at": "2026-08-06T10:00:00Z",
            "digest": {"algorithm": "sha256", "value": "ff"},
            "binding": {"message_ids": ["m1"]},
        },
    })
    assert got["last_verified"] == "2026-08-06T10:00:00Z"
    assert got["origin_session"] == "aaaa-1111"
    assert got["source_message_ids"] == ["m1", "m2"]
    assert got["quote_provenance"] == {
        "verifier": "tier-f", "verifier_version": 1, "outcome": "verified",
        "checked_at": "2026-08-06T10:00:00Z",
        "digest_algorithm": "sha256", "message_ids": ["m1"],
    }

def test_norm_item_trust_anatomy_fields_default_none():
    got = reader._norm_item({"text": "x"})
    assert got["last_verified"] is None
    assert got["origin_session"] is None
    assert got["source_message_ids"] is None
    assert got["quote_provenance"] is None

def test_norm_item_junk_trust_anatomy_shapes_become_none():
    got = reader._norm_item({
        "text": "x",
        "last_verified": 123,
        "origin_session": ["not", "a", "str"],
        "source_message_ids": "m1",              # str, not list
        "quote_provenance": "not a dict",
    })
    assert got["last_verified"] is None
    assert got["origin_session"] is None
    assert got["source_message_ids"] is None
    assert got["quote_provenance"] is None

def test_norm_provenance_malformed_nested_shapes():
    got = reader._norm_item({"text": "x", "quote_provenance": {
        "verifier": 7, "outcome": "", "checked_at": None,
        "digest": "not-a-dict", "binding": {"message_ids": ["ok", 5]},
    }})
    assert got["quote_provenance"] == {
        "verifier": None, "verifier_version": None, "outcome": None,
        "checked_at": None, "digest_algorithm": None, "message_ids": ["ok"],
    }

def test_norm_item_source_message_ids_filters_non_strings():
    got = reader._norm_item({"text": "x", "source_message_ids": ["a", 1, "b"]})
    assert got["source_message_ids"] == ["a", "b"]

def test_norm_provenance_accepts_a_legacy_string_verifier():
    """Older receipts recorded the verifier as a bare string. A tolerant reader keeps
    reading them; only the version is unavailable."""
    got = reader._norm_item({"text": "x", "quote_provenance": {
        "verifier": "quote-v2", "outcome": "verified",
    }})
    assert got["quote_provenance"]["verifier"] == "quote-v2"
    assert got["quote_provenance"]["verifier_version"] is None

def test_norm_provenance_verifier_object_without_version():
    got = reader._norm_item({"text": "x", "quote_provenance": {
        "verifier": {"id": "tier-f"}, "outcome": "verified",
    }})
    assert got["quote_provenance"]["verifier"] == "tier-f"
    assert got["quote_provenance"]["verifier_version"] is None
