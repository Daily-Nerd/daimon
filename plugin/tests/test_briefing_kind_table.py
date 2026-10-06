"""The briefing's section tables derive from `SECTION_FIELD` and
`schema.ITEM_FIELDS` (#1132 PR 4); their values are exactly what the
hand-kept literals held."""

from daimon_briefing import briefing, schema, scoring

NOW = 1_800_000_000.0


def test_section_field_names_every_section_and_a_real_field():
    fields = {(f.section, f.key) for f in schema.ITEM_FIELDS}
    assert set(briefing.SECTION_FIELD) == set(briefing.SECTION_ORDER)
    assert set(briefing.SECTION_FIELD.values()) <= fields
    assert set(briefing.SECTION_FIELD.values()) == fields


def test_external_and_open_loops_are_two_halves_of_one_field():
    assert (briefing.SECTION_FIELD["external"]
            == briefing.SECTION_FIELD["open_loops"]
            == ("working_context", "open_questions"))
    assert briefing.SECTION_FIELD["active_topic"] == (
        "working_context", "active_topic")


def test_item_sections_are_section_order_minus_the_singleton():
    assert briefing._ITEM_SECTIONS == (
        "decisions", "external", "open_loops", "beliefs", "uncertainties",
        "contradictions")


def test_briefable_sections_are_those_whose_field_is_briefable():
    assert briefing.BRIEFABLE_SECTIONS == frozenset(
        {"external", "open_loops", "uncertainties"})


def test_weight_types_are_what_the_literal_held():
    assert briefing._WEIGHT_TYPE == {
        "decisions": "recent_decision", "external": "open_question",
        "open_loops": "open_question", "beliefs": "strong_belief",
        "uncertainties": "uncertainty", "contradictions": "contradiction"}


def test_a_contradiction_still_weighs_by_the_default_rules():
    """contradictions_flagged has no scoring type, so its section's weight type
    is not a TYPE_RULES key and every lookup falls back to the default
    rules. That fallback is the behavior; derivation must not change it."""
    item = {"text": "c", "importance": 7, "trust": "inferred",
            "first_seen": "2026-01-01T00:00:00Z"}
    got = scoring.effective_weight(
        item, briefing._WEIGHT_TYPE["contradictions"], NOW)
    assert got == scoring.effective_weight(item, "recent_decision", NOW)
    assert got == scoring.effective_weight(item, "contradiction", NOW)
