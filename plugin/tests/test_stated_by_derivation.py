"""#893: `stated_by` is DERIVED from the transcript, never accepted from a host.

`#892` shipped `items.stated_by` readable, indexed and rendered, and unfillable:
the field is code-owned and every path accepting a checkpoint payload strips it,
so no host could ever put a value in.

The fix is not a write channel. A host naming an ITEM's speaker asserts something
about content it did not author. A host putting a speaker on a MESSAGE states a
fact about an artifact it received. So the host supplies `said_by` per message,
daimon owns the join through the already-validated `source_message_ids` binding,
and the derivation runs AFTER `strip_code_owned_keys` so a model still cannot
reach the field.

The rule inherited from #892 and extended here: ABSENT MEANS UNKNOWN. An item
with no binding, an unknown speaker, or bindings that DISAGREE all render
nothing, because ambiguous is not known.
"""

from daimon_briefing import serializer


def _cp_one_decision(item):
    return {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [item],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


def _item(cp):
    return cp["working_context"]["recent_decisions"][0]


# ---- the map ---------------------------------------------------------------


def test_message_speakers_by_id_maps_host_id_to_speaker():
    messages = [
        {"id": "u1", "role": "user", "said_by": "ana", "content": "a"},
        {"id": "u2", "role": "user", "said_by": "ben", "content": "b"},
    ]
    assert serializer.message_speakers_by_id(messages) == {"u1": "ana",
                                                           "u2": "ben"}


def test_message_speakers_by_id_skips_messages_without_a_speaker():
    """A host with no speaker on its messages degrades to an empty map, the
    same mode signal_message_ids documents for hosts with no tool rows."""
    messages = [
        {"id": "u1", "role": "user", "content": "a"},
        {"id": "u2", "role": "user", "said_by": "  ", "content": "b"},
        {"role": "user", "said_by": "ana", "content": "no id"},
    ]
    assert serializer.message_speakers_by_id(messages) == {}


def test_message_speakers_by_id_tolerates_junk():
    """Never fatal, same philosophy as sanitize_source_ids."""
    assert serializer.message_speakers_by_id(None) == {}
    assert serializer.message_speakers_by_id(["not a dict", 7]) == {}
    assert serializer.message_speakers_by_id(
        [{"id": "u1", "said_by": 7}]) == {}


# ---- the join --------------------------------------------------------------


def test_derives_stated_by_from_a_validated_binding():
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1"]})
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert _item(cp)["stated_by"] == "ana"


def test_an_item_with_no_binding_gets_nothing():
    cp = _cp_one_decision({"text": "d", "trust": "inferred"})
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert "stated_by" not in _item(cp)


def test_an_unknown_speaker_gets_nothing():
    """The binding is valid but the host named no speaker for that message."""
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u9"]})
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert "stated_by" not in _item(cp)


def test_agreeing_bindings_derive_the_shared_speaker():
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1", "u2"]})
    serializer.derive_stated_by(cp, {"u1": "ana", "u2": "ana"})
    assert _item(cp)["stated_by"] == "ana"


def test_disagreeing_bindings_derive_nothing():
    """AMBIGUOUS IS NOT KNOWN. An item stitched from two speakers has no single
    stater, and picking one would manufacture the misattribution the field
    exists to prevent. Absent is the honest answer."""
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1", "u2"]})
    serializer.derive_stated_by(cp, {"u1": "ana", "u2": "ben"})
    assert "stated_by" not in _item(cp)


def test_a_partially_known_pair_derives_nothing():
    """One binding names ana, the other names nobody. We cannot say the item is
    ana's when part of it came from a message we cannot attribute."""
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1", "u9"]})
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert "stated_by" not in _item(cp)


def test_an_empty_speaker_map_derives_nothing_anywhere():
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1"]})
    serializer.derive_stated_by(cp, {})
    assert "stated_by" not in _item(cp)


# ---- the guard -------------------------------------------------------------


def test_a_model_supplied_value_never_survives():
    """The wedge principle. strip_code_owned_keys runs first and removes the
    model's claim; derivation then fills from the transcript or not at all. A
    forged value must not persist just because the item has no binding."""
    cp = _cp_one_decision({"text": "d", "trust": "inferred",
                           "stated_by": "someone-who-never-spoke"})
    serializer.strip_code_owned_keys(cp)
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert "stated_by" not in _item(cp)


def test_a_forged_value_is_replaced_by_the_derived_one():
    """A model naming the RIGHT speaker still does not get credit for it: the
    stored value comes from the transcript join, never from model output."""
    cp = _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                           "source_message_ids": ["u1"], "stated_by": "ben"})
    serializer.strip_code_owned_keys(cp)
    serializer.derive_stated_by(cp, {"u1": "ana"})
    assert _item(cp)["stated_by"] == "ana"


# ---- the wiring ------------------------------------------------------------


def test_the_pipeline_actually_calls_the_derivation():
    """#892's defect was a field nothing could write. The mirror defect is a
    derivation nothing calls, so this pins the call site rather than trusting
    that it exists.

    Same discipline as test_stripped_item_keys_match_the_serializer_strip_list:
    two things that must agree, asserted rather than assumed."""
    import inspect
    src = inspect.getsource(serializer)
    body = src[src.index("sanitize_source_ids(checkpoint, message_id_map"):]
    head = body[:900]
    assert "derive_stated_by(checkpoint, message_speakers_by_id(messages))" in head, (
        "derive_stated_by is not called right after sanitize_source_ids in the "
        "serialize pipeline; the field would ship unfillable again")


def test_transcript_carries_a_host_speaker_through_normalization():
    """transcript.py normalizes to a fixed key set, so an unrecognised key on
    the host row is dropped. Without this pass-through the derivation joins
    against an empty map forever."""
    import json

    from daimon_briefing import transcript
    rows = [
        {"type": "user", "uuid": "u1", "said_by": "ana",
         "message": {"role": "user", "content": "hello"}},
        {"type": "user", "uuid": "u2",
         "message": {"role": "user", "content": "no speaker"}},
    ]
    msgs = transcript._from_jsonl("\n".join(json.dumps(r) for r in rows))
    assert serializer.message_speakers_by_id(msgs) == {"u1": "ana"}
