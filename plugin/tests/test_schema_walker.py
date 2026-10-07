"""`schema.iter_items` / `schema.iter_fields`: the one walker over the item
fields of a checkpoint (#1132 PR 4), and the `briefable` column."""

from daimon_briefing import schema, serializer


def _checkpoint(**sections):
    return {"working_context": {
        "active_topic": {"text": "topic"},
        "open_questions": [{"text": "q1"}, {"text": "q2"}],
        "recent_decisions": [{"text": "d1"}],
    }, "epistemic_snapshot": {
        "strong_beliefs": [{"text": "b1"}],
        "uncertainties": [{"text": "u1"}],
        "contradictions_flagged": [{"text": "c1"}],
    }, **sections}


def test_briefable_marks_exactly_the_loop_shaped_fields():
    assert {f.key for f in schema.ITEM_FIELDS if f.briefable} == {
        "open_questions", "uncertainties"}


def test_iter_items_yields_field_and_item_in_table_order():
    got = [(f.key, i["text"]) for f, i in schema.iter_items(_checkpoint())]
    assert got == [("active_topic", "topic"), ("open_questions", "q1"),
                   ("open_questions", "q2"), ("recent_decisions", "d1"),
                   ("strong_beliefs", "b1"), ("uncertainties", "u1"),
                   ("contradictions_flagged", "c1")]


def test_iter_items_yields_the_item_objects_themselves():
    cp = _checkpoint()
    first = cp["working_context"]["open_questions"][0]
    assert any(i is first for _f, i in schema.iter_items(cp))


def test_an_absent_block_is_skipped():
    cp = {"working_context": {"open_questions": [{"text": "q"}]}}
    assert [i["text"] for _f, i in schema.iter_items(cp)] == ["q"]
    assert list(schema.iter_items({})) == []


def test_a_non_dict_block_is_skipped_not_raised():
    cp = _checkpoint()
    cp["epistemic_snapshot"] = "torn"
    assert [f.key for f, _i in schema.iter_items(cp)] == [
        "active_topic", "open_questions", "open_questions",
        "recent_decisions"]
    assert list(schema.iter_items({"working_context": ["x"]})) == []


def test_a_singleton_that_is_not_a_dict_is_skipped_in_both_modes():
    cp = _checkpoint()
    cp["working_context"]["active_topic"] = "a bare string"
    for dicts_only in (True, False):
        keys = [f.key for f, _i in schema.iter_items(
            cp, dicts_only=dicts_only)]
        assert "active_topic" not in keys


def test_a_bare_string_contradiction_is_skipped_by_default():
    cp = _checkpoint()
    cp["epistemic_snapshot"]["contradictions_flagged"] = [
        "raw string", {"text": "c1"}]
    got = [i for f, i in schema.iter_items(cp)
           if f.key == "contradictions_flagged"]
    assert got == [{"text": "c1"}]


def test_dicts_only_false_yields_list_entries_as_they_are():
    cp = _checkpoint()
    cp["epistemic_snapshot"]["contradictions_flagged"] = [
        "raw string", {"text": "c1"}]
    got = [i for f, i in schema.iter_items(cp, dicts_only=False)
           if f.key == "contradictions_flagged"]
    assert got == ["raw string", {"text": "c1"}]


def test_a_list_field_holding_a_non_list_is_skipped():
    cp = _checkpoint()
    cp["working_context"]["open_questions"] = "not a list"
    assert "open_questions" not in [
        f.key for f, _i in schema.iter_items(cp, dicts_only=False)]


def test_iter_fields_yields_field_and_raw_value_behind_the_block_guard():
    cp = _checkpoint()
    cp["epistemic_snapshot"]["uncertainties"] = None
    got = {f.key: v for f, v in schema.iter_fields(cp)}
    assert got["open_questions"] == [{"text": "q1"}, {"text": "q2"}]
    assert got["active_topic"] == {"text": "topic"}
    assert got["uncertainties"] is None


def test_iter_fields_skips_a_non_dict_block():
    cp = _checkpoint()
    cp["working_context"] = "torn"
    assert [f.key for f, _v in schema.iter_fields(cp)] == [
        "strong_beliefs", "uncertainties", "contradictions_flagged"]


def test_serializer_iter_items_is_the_walker_without_the_field():
    cp = _checkpoint()
    assert list(serializer.iter_items(cp)) == [
        i for _f, i in schema.iter_items(cp)]


def test_anchor_and_trusted_quotes_walk_through_the_schema_walker():
    from daimon_briefing import anchor, briefing
    cp = _checkpoint()
    cp["epistemic_snapshot"]["contradictions_flagged"] = ["raw", {"text": "c"}]
    cp["working_context"]["recent_decisions"] = [
        {"text": "d", "trust": "verbatim", "quote": " exact words "}]
    # anchor keeps bare strings (drifted() skips them per item) ...
    assert "raw" in list(anchor._all_items(cp))
    # ... and the quote walk sees verbatim items only, dicts only.
    assert list(briefing._iter_trusted_quotes(cp)) == ["exact words"]


def test_anchor_and_trusted_quotes_survive_a_non_dict_block():
    from daimon_briefing import anchor, briefing
    cp = {"working_context": "torn", "epistemic_snapshot": ["x"]}
    assert list(anchor._all_items(cp)) == []
    assert list(briefing._iter_trusted_quotes(cp)) == []


def test_a_checkpoint_that_is_not_a_dict_yields_nothing():
    for cp in ("torn", None, ["x"], 7):
        assert list(schema.iter_fields(cp)) == []
        assert list(schema.iter_items(cp)) == []


def test_value_fields_are_the_three_content_columns():
    assert schema.VALUE_FIELDS == ("text", "quote", "scene")
