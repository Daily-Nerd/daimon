"""#829: record cross-message / cross-role quote stitching on the receipt.

SERIALIZE_SYS rule 17 forbids stitching a quote from different speakers or
turns, but quote_matches scans one flattened haystack, so a stitched quote
verifies and earns trust=verbatim. This slice is recording only, additive and
behavior-preserving: the receipt says whether the matched fragments could have
come from one message / one role, verification outcomes stay untouched, and
the violation rate becomes measurable before any semantics change.

Necessity semantics (pinned here): a flag is True only when NO single message
(or no single role's messages, joined in transcript order) can account for
every matched fragment — never merely because the flat scan happened to cross
a boundary it did not need to cross.
"""
from daimon_briefing import field_table, provenance, serializer

_HASH = "a" * 64


def _source(session="S-new"):
    return {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session,
        "locator": "managed",
        "author": "alice",
    }


def _checkpoint(item):
    return {
        "session_id": "S-new",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [item],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _verify(item, messages):
    cp = _checkpoint(item)
    serializer.verify_quotes(
        cp, serializer._render_transcript(messages), messages,
        source_ref=_source(), transcript_hash=_HASH)
    return item


# ---- single-source quotes carry an explicit all-clear ----

def test_single_message_quote_records_no_stitching():
    messages = [
        {"role": "user", "content": "please keep the invariant intact", "id": "u-1"},
        {"role": "assistant", "content": "the invariant holds after the change",
         "id": "a-2"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "the invariant holds after the change"}
    _verify(item, messages)

    assert item["quote_verified"] is True
    assert item["quote_provenance"]["stitching"] == {
        "cross_message": False, "cross_role": False}


def test_scoped_single_message_binding_records_no_stitching():
    messages = [
        {"role": "user", "content": "the durable sentence we agreed on", "id": "u-1"},
        {"role": "assistant", "content": "unrelated follow-up prose", "id": "a-2"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "the durable sentence we agreed on",
            "source_message_ids": ["u-1"]}
    _verify(item, messages)

    receipt = item["quote_provenance"]
    assert receipt["binding"]["mode"] == "message-ids"
    assert receipt["stitching"] == {"cross_message": False, "cross_role": False}


# ---- stitched quotes are recorded, and still verify (recording only) ----

def test_cross_message_same_role_stitch_is_recorded():
    messages = [
        {"role": "user", "content": "the first constraint half lives here",
         "id": "u-1"},
        {"role": "assistant", "content": "acknowledged, moving on", "id": "a-2"},
        {"role": "user", "content": "the second constraint half arrives now",
         "id": "u-3"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "first constraint half ... second constraint half"}
    _verify(item, messages)

    # Behavior preserved: the stitched quote still verifies (rule 17 stays
    # doctrine; enforcement is the measured follow-up, not this slice).
    assert item["trust"] == "verbatim"
    assert item["quote_verified"] is True
    assert item["quote_provenance"]["stitching"] == {
        "cross_message": True, "cross_role": False}


def test_cross_role_stitch_is_recorded():
    messages = [
        {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
        {"role": "assistant", "content": "the omega conclusion follows cleanly",
         "id": "a-2"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "alpha premise stands ... omega conclusion follows"}
    _verify(item, messages)

    assert item["quote_verified"] is True
    assert item["quote_provenance"]["stitching"] == {
        "cross_message": True, "cross_role": True}


def test_scoped_two_message_binding_attributes_within_the_cited_set():
    messages = [
        {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
        {"role": "assistant", "content": "the omega conclusion follows cleanly",
         "id": "a-2"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "alpha premise stands ... omega conclusion follows",
            "source_message_ids": ["u-1", "a-2"]}
    _verify(item, messages)

    receipt = item["quote_provenance"]
    assert receipt["binding"]["mode"] == "message-ids"
    assert receipt["stitching"] == {"cross_message": True, "cross_role": True}


# ---- honest absence everywhere attribution is impossible or meaningless ----

def test_not_verified_receipt_carries_no_stitching():
    messages = [
        {"role": "user", "content": "nothing that matches lives here", "id": "u-1"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "a sentence the transcript never said"}
    _verify(item, messages)

    assert item["quote_verified"] is False
    assert "stitching" not in item["quote_provenance"]


def test_legacy_two_arg_call_records_no_stitching():
    """Without `messages` there is no per-message view to attribute against —
    absent = unknown, the project convention, never a guessed False."""
    item = {"text": "claim", "trust": "verbatim",
            "quote": "the durable sentence we agreed on"}
    cp = _checkpoint(item)
    serializer.verify_quotes(
        cp, "user: the durable sentence we agreed on",
        source_ref=_source(), transcript_hash=_HASH)

    assert item["quote_verified"] is True
    assert "stitching" not in item["quote_provenance"]


def test_stitched_receipt_round_trips_the_validator():
    messages = [
        {"role": "user", "content": "the alpha premise stands firm", "id": "u-1"},
        {"role": "assistant", "content": "the omega conclusion follows cleanly",
         "id": "a-2"},
    ]
    item = {"text": "claim", "trust": "verbatim",
            "quote": "alpha premise stands ... omega conclusion follows"}
    _verify(item, messages)

    assert provenance.valid_quote_receipt(item["quote_provenance"])


# ---- attribution is impossible: the helper answers None, never a guess ----

def test_stitching_flags_are_none_when_attribution_is_impossible():
    candidates = [("user", "the alpha premise stands firm")]
    # No usable fragment (below _MIN_FRAGMENT after normalization).
    assert serializer._stitching_flags("short", candidates) is None
    # Non-str quote fails closed the same way quote_matches does.
    assert serializer._stitching_flags(None, candidates) is None
    # No candidate carries any text (all blanked/stripped).
    assert serializer._stitching_flags(
        "a proper long fragment", [("user", ""), ("assistant", "")]) is None


# ---- validator shape rules for the new optional member ----

def _receipt(**kw):
    return provenance.quote_receipt(
        _source(), {"algorithm": "sha256", "scope": "raw-file", "value": _HASH},
        outcome="verified", checked_at="2026-08-29T10:00:00Z",
        binding_mode="transcript-scan", **kw)


def test_quote_receipt_emits_normalized_stitching():
    receipt = _receipt(stitching={"cross_message": True, "cross_role": False})
    assert receipt is not None
    assert receipt["stitching"] == {"cross_message": True, "cross_role": False}


def test_quote_receipt_without_stitching_stays_bare():
    receipt = _receipt()
    assert receipt is not None
    assert "stitching" not in receipt


def test_valid_quote_receipt_rejects_malformed_stitching():
    good = _receipt(stitching={"cross_message": False, "cross_role": False})
    assert provenance.valid_quote_receipt(good)

    for bad in ("yes", 1, [], {"cross_message": True},
                {"cross_message": "yes", "cross_role": False},
                {"cross_message": True, "cross_role": None}):
        receipt = _receipt()
        receipt["stitching"] = bad
        assert not provenance.valid_quote_receipt(receipt), bad


def test_receipt_without_stitching_is_still_valid_for_legacy_checkpoints():
    receipt = _receipt()
    receipt.pop("stitching", None)
    assert provenance.valid_quote_receipt(receipt)


# ---- the published contract learns the new shape (#827 table) ----

def test_field_table_documents_the_stitching_shape():
    row = field_table.rule("item", "quote_provenance")
    shape = " ".join(dict(row.constraints)["shape"])
    assert "stitching" in shape
    assert "cross_message" in shape
    assert "cross_role" in shape
