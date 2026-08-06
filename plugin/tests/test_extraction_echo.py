"""#577: daimon's own tool output must not reach the extractor.

The echo defense (#440/#441/#512) was quote-scoped. `stripped_transcript`
blanked a `daimon_output` row so verification could not accept daimon as a
witness for its own claim, but the extractor kept reading the same rows raw.
An extracted item carries `text`, `quote`, `because` and `scene`, and only
`quote` is verified, so daimon's output could ride in through the other three
and inherit `verbatim` from an unrelated real sentence.

Measured on a real serialize of guard output: the derived belief was correctly
downgraded to `inferred` with `quote_echo_only`, and the derived decision
landed `verbatim` with `because` holding the guard verdict word for word.
"""
from daimon_briefing import serializer


def _msgs():
    return [
        {"role": "user", "content": "should we add a cache here?", "id": "m1"},
        {"role": "tool", "content": "REFUTED: caching was rolled back in March",
         "id": "m2", "tool_result": True, "daimon_output": True},
        {"role": "assistant", "content": "No, that route is already refuted.",
         "id": "m3"},
    ]


def test_extractor_never_receives_daimon_output():
    rendered = serializer._render_transcript(
        serializer.extraction_messages(_msgs()))
    assert "rolled back in March" not in rendered
    # Everything else survives untouched.
    assert "should we add a cache here?" in rendered
    assert "No, that route is already refuted." in rendered


def test_blanking_preserves_message_positions():
    # [mN] markers are positional over the full list (#358). Dropping a row
    # instead of blanking it would renumber every later citation, silently
    # repointing quotes that were already verified against the old numbering.
    before = serializer._render_transcript(_msgs())
    after = serializer._render_transcript(
        serializer.extraction_messages(_msgs()))
    assert before.count("[m") == after.count("[m")
    for marker in ("[m1]", "[m2]", "[m3]"):
        assert (marker in before) == (marker in after)


def test_extraction_messages_does_not_mutate_its_input():
    messages = _msgs()
    serializer.extraction_messages(messages)
    assert messages[1]["content"] == "REFUTED: caching was rolled back in March"


def test_verification_haystack_is_unchanged_by_this_fix():
    # The two halves now agree, and the verification side must still blank the
    # same row it always did. A regression here would re-open #512.
    haystack = serializer.stripped_transcript(_msgs())
    assert "rolled back in March" not in haystack
    assert "No, that route is already refuted." in haystack


def test_rows_without_the_flag_are_passed_through_by_identity():
    # Provenance, never shape (deadend 0020). An ordinary tool row that merely
    # looks like daimon output is not daimon output.
    messages = [{"role": "tool", "content": "REFUTED: something", "id": "m1",
                 "tool_result": True}]
    assert serializer.extraction_messages(messages)[0] is messages[0]


def test_non_dict_rows_survive():
    assert serializer.extraction_messages([None, "x"]) == [None, "x"]
    assert serializer.extraction_messages(None) == []
