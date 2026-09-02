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
    assert "derive_stated_by(checkpoint, message_speakers_by_id(" in head, (
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


# ---- #896: per-session fallback --------------------------------------------
#
# #894 needs the host to author the transcript, and a host that shells out to
# `claude -p` never does: no row carries `said_by`, the map is empty, and the
# derivation is inert. #896 lets such a host declare ONE speaker for the whole
# session, out of band, used only where the precise per-message path has
# nothing. The role gate lives in what the map CONTAINS: a session default
# applied blindly would put the human's name on the assistant's sentences,
# which is the misattribution the field exists to prevent.


def _mixed_rows():
    return [
        {"id": "u1", "role": "user", "content": "a"},
        {"id": "a2", "role": "assistant", "content": "b"},
    ]


def test_the_session_speaker_fills_user_rows_with_no_said_by():
    assert serializer.message_speakers_by_id(
        [{"id": "u1", "role": "user", "content": "a"}],
        session_speaker="ana") == {"u1": "ana"}


def test_a_per_message_said_by_wins_over_the_session_speaker():
    """The precise signal beats the coarse one on the same row. A host that
    knows WHO sent this message has said something the session default cannot
    contradict."""
    assert serializer.message_speakers_by_id(
        [{"id": "u1", "role": "user", "said_by": "ben", "content": "a"}],
        session_speaker="ana") == {"u1": "ben"}


def test_non_user_rows_are_omitted_even_with_a_session_speaker():
    """Assistant rows, tool rows, and rows with no role at all stay OUT of the
    map. An item's binding usually points at an assistant message (a verbatim
    quote is normally the assistant's words), so a blind session default would
    attribute the assistant's sentences to the human."""
    messages = [
        {"id": "a1", "role": "assistant", "content": "a"},
        {"id": "t2", "role": "tool", "content": "b"},
        {"id": "x3", "content": "no role at all"},
        {"id": "u4", "role": "user", "content": "d"},
    ]
    assert serializer.message_speakers_by_id(
        messages, session_speaker="ana") == {"u4": "ana"}


def test_a_non_user_row_with_its_own_said_by_still_maps():
    """The omission is the FALLBACK's rule, not the map's. A host that names
    the speaker of an assistant row has stated a fact about its own artifact,
    and #894's path is unchanged."""
    messages = [{"id": "a1", "role": "assistant", "said_by": "bot",
                 "content": "a"}]
    assert serializer.message_speakers_by_id(
        messages, session_speaker="ana") == {"a1": "bot"}


def test_no_session_speaker_is_byte_identical_to_today():
    """The default path must not move. None, empty, and whitespace all mean
    the host declared nothing."""
    messages = _mixed_rows() + [
        {"id": "u3", "role": "user", "said_by": "ben", "content": "c"}]
    baseline = serializer.message_speakers_by_id(messages)
    assert baseline == {"u3": "ben"}
    for blank in (None, "", "   ", "\t\n"):
        assert serializer.message_speakers_by_id(
            messages, session_speaker=blank) == baseline


def test_a_non_string_session_speaker_is_ignored():
    """Never fatal, same philosophy as the rest of this module."""
    assert serializer.message_speakers_by_id(
        _mixed_rows(), session_speaker=7) == {}


def test_the_session_speaker_is_stripped():
    assert serializer.message_speakers_by_id(
        [{"id": "u1", "role": "user", "content": "a"}],
        session_speaker="  ana  ") == {"u1": "ana"}


# ---- #896 through the join: the four rows that matter ----------------------


def _fallback_map(messages):
    return serializer.message_speakers_by_id(messages, session_speaker="ana")


def _bound(*ids):
    return _cp_one_decision({"text": "d", "trust": "verbatim", "quote": "q",
                             "source_message_ids": list(ids)})


def test_an_item_bound_to_one_user_message_is_attributed():
    cp = _bound("u1")
    serializer.derive_stated_by(cp, _fallback_map(_mixed_rows()))
    assert _item(cp)["stated_by"] == "ana"


def test_an_item_bound_to_two_user_messages_is_attributed():
    """Unanimity holds trivially: one session, one declared speaker."""
    messages = [{"id": "u1", "role": "user", "content": "a"},
                {"id": "u2", "role": "user", "content": "b"}]
    cp = _bound("u1", "u2")
    serializer.derive_stated_by(cp, _fallback_map(messages))
    assert _item(cp)["stated_by"] == "ana"


def test_an_item_bound_to_a_user_and_an_assistant_message_gets_nothing():
    """THE POINT OF THE ROLE GATE. The assistant id is absent from the map, so
    the set is {"ana", None} and derive_stated_by's existing unanimity rule
    refuses the item for free. No new gate was needed."""
    cp = _bound("u1", "a2")
    serializer.derive_stated_by(cp, _fallback_map(_mixed_rows()))
    assert "stated_by" not in _item(cp)


def test_an_item_bound_only_to_an_assistant_message_gets_nothing():
    cp = _bound("a2")
    serializer.derive_stated_by(cp, _fallback_map(_mixed_rows()))
    assert "stated_by" not in _item(cp)


# ---- #896 end to end -------------------------------------------------------


def _messages_with_ids(n=20):
    """make_messages, plus the host ids the binding path needs. Message i is
    marker m{i+1}; even indices are user rows, odd ones assistant."""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"id": f"{role[0]}{i + 1}", "role": role,
                    "content": f"line {i} from {role}"})
    return out


def _two_bound_decisions():
    import json
    return json.dumps({
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": "from the human", "trust": "verbatim",
                 "quote": "line 0 from user", "source_message_ids": ["m1"]},
                {"text": "from the model", "trust": "verbatim",
                 "quote": "line 1 from assistant",
                 "source_message_ids": ["m2"]},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [],
            "contradictions_flagged": [],
        },
        "worker_queue": [],
    })


def test_the_pipeline_applies_the_declared_session_speaker(
        fake_chat_factory, monkeypatch):
    """The wiring assertion above only proves a call site exists. This proves
    the env var actually reaches the map through a real serialize, on a
    transcript with NO said_by anywhere — the claude -p shape #896 exists for.
    """
    monkeypatch.setenv("DAIMON_SESSION_SPEAKER", "ana")
    chat = fake_chat_factory(_two_bound_decisions())
    cp = serializer.serialize_strict("S1", _messages_with_ids(), chat=chat)
    items = cp["working_context"]["recent_decisions"]
    assert items[0]["stated_by"] == "ana"
    assert "stated_by" not in items[1], (
        "the assistant-bound item was attributed to the human; the role gate "
        "is not holding through the pipeline")


def test_the_pipeline_derives_nothing_without_the_env_var(
        fake_chat_factory, monkeypatch):
    """The guard on the test above: same transcript, same checkpoint, no
    declaration. A fallback that leaked in unasked would attribute items on
    every host in the world."""
    monkeypatch.delenv("DAIMON_SESSION_SPEAKER", raising=False)
    chat = fake_chat_factory(_two_bound_decisions())
    cp = serializer.serialize_strict("S1", _messages_with_ids(), chat=chat)
    for item in cp["working_context"]["recent_decisions"]:
        assert "stated_by" not in item
