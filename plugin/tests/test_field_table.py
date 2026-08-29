"""#827: the declarative field table is the single source of truth.

Three guarantees, each pinned here:

1. The generated validator's verdict AND reason string are byte-identical to
   the live serializer validator's, across a matrix that exercises every
   predicate branch — so table-vs-code divergence is a test failure, never a
   silent consumer-side data loss.
2. The generated normalizers reproduce sanitize_importance / sanitize_scene
   exactly (the #827 motivating bug was a consumer guessing importance 1-5
   against the producer's 1-10).
3. The published schema document (docs/checkpoint-schema.json) is exactly what
   the table renders, versioned with the envelope's format_version — a stale
   committed artifact fails CI.
"""
import copy
import json
from pathlib import Path

import pytest

from daimon_briefing import field_table, schema, serializer

_REPO = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO / "docs" / "checkpoint-schema.json"


def _item(**over):
    base = {"text": "t", "trust": "inferred"}
    base.update(over)
    return base


def _checkpoint(**over):
    base = {
        "session_id": "S1",
        "working_context": {
            "active_topic": {"text": "topic", "trust": "inferred"},
            "open_questions": [{"text": "q", "trust": "inferred"}],
            "recent_decisions": [
                {"text": "d", "trust": "verbatim", "quote": "the quote"}],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [{"text": "u", "trust": "inferred"}],
            "contradictions_flagged": [],
        },
        "worker_queue": [],
    }
    base.update(over)
    return base


# ---- 1. generated item validator == live item validator ----

_ITEM_CASES = (
    _item(),
    _item(trust="verbatim", quote="q"),
    _item(text=""),                          # empty text stays valid (#134)
    "not a dict",
    None,
    42,
    {"trust": "inferred"},                   # missing text
    {"text": "t"},                           # missing trust
    {},                                      # missing both
    _item(text=None),
    _item(text=7),
    _item(trust="gospel"),
    _item(trust=None),
    _item(trust="verbatim"),                 # verbatim without quote
    _item(trust="verbatim", quote=""),
    _item(trust="verbatim", quote="   "),
    _item(trust="verbatim", quote=None),
    _item(trust="verbatim", quote=3),
    _item(because="stated reasoning"),
    _item(because=None),                     # null because is tolerated
    _item(because=5),
    _item(because=["x"]),
    _item(anchored_to={"file": "f", "symbol": "s", "body_hash": "h"}),
    _item(anchored_to=None),                 # null anchored_to is tolerated
    _item(anchored_to="f:s"),
    _item(anchored_to={"file": "f", "symbol": "s"}),
    _item(anchored_to={"file": "f", "symbol": "s", "body_hash": ""}),
    _item(anchored_to={"file": "f", "symbol": "s", "body_hash": 9}),
    # out-of-contract values on pass/clamp fields never reject an item
    _item(importance="high", scene=7, external_state="yes", links="x",
          source_message_ids=13),
)


@pytest.mark.parametrize("item", _ITEM_CASES,
                         ids=[f"case{i}" for i in range(len(_ITEM_CASES))])
def test_item_reason_matches_the_live_validator(item):
    assert field_table.item_reason(item) == serializer._item_reason(item)


def test_item_reason_strings_are_the_live_predicate_names():
    """The exact strings ship in SchemaValidationError and retry notes (#743),
    so the generated validator must reproduce them byte-for-byte."""
    assert field_table.item_reason("x") == "not a dict"
    assert field_table.item_reason({}) == "missing text or trust"
    assert field_table.item_reason(_item(text=None)) == "text is not a str"
    assert (field_table.item_reason(_item(trust="gospel"))
            == "trust is not a known trust class")
    assert (field_table.item_reason(_item(trust="verbatim"))
            == "trust=verbatim item has no quote")
    assert field_table.item_reason(_item(because=5)) == "because is not a str"
    assert (field_table.item_reason(_item(anchored_to="x"))
            == "anchored_to is not a dict")
    assert (field_table.item_reason(_item(anchored_to={}))
            == "anchored_to is missing file, symbol, or body_hash")


# ---- 2. generated checkpoint validator == live checkpoint validator ----

def _without(key):
    def mutate(cp):
        del cp[key]
        return cp
    return mutate


def _set(path, value):
    def mutate(cp):
        node = cp
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
        return cp
    return mutate


def _del(path):
    def mutate(cp):
        node = cp
        for part in path[:-1]:
            node = node[part]
        del node[path[-1]]
        return cp
    return mutate


_CHECKPOINT_MUTATIONS = (
    lambda cp: cp,
    lambda cp: "not a dict",
    lambda cp: None,
    _without("session_id"),
    _set(("working_context",), "junk"),
    _without("working_context"),
    _set(("epistemic_snapshot",), None),
    _without("epistemic_snapshot"),
    _del(("working_context", "active_topic")),
    _set(("working_context", "active_topic"), {"trust": "inferred"}),
    _set(("working_context", "open_questions"), "junk"),
    _del(("working_context", "open_questions")),
    _set(("working_context", "open_questions"), [{"text": "q"}]),
    _set(("working_context", "recent_decisions"), None),
    _set(("working_context", "recent_decisions"),
         [{"text": "d", "trust": "verbatim"}]),
    _set(("epistemic_snapshot", "strong_beliefs"), "junk"),
    _del(("epistemic_snapshot", "strong_beliefs")),   # optional: absent is valid
    _set(("epistemic_snapshot", "uncertainties"), [{"text": "u", "trust": "x"}]),
    _set(("epistemic_snapshot", "contradictions_flagged"), "anything"),
    _del(("working_context", "active_topic")),
    # active_topic missing AND a bad list: presence is checked first
    lambda cp: (_set(("working_context", "open_questions"), "junk")(
        _del(("working_context", "active_topic"))(cp))),
    # bad wc list AND bad active_topic item: the wc lists are checked first
    lambda cp: (_set(("working_context", "active_topic"), {"text": 1, "trust": "inferred"})(
        _set(("working_context", "recent_decisions"), [{"text": "d", "trust": "z"}])(cp))),
    _without("worker_queue"),
)


@pytest.mark.parametrize(
    "mutate", _CHECKPOINT_MUTATIONS,
    ids=[f"case{i}" for i in range(len(_CHECKPOINT_MUTATIONS))])
def test_checkpoint_reason_matches_the_live_validator(mutate):
    ours = field_table.checkpoint_reason(mutate(_checkpoint()))
    live = serializer.validation_reason(mutate(_checkpoint()))
    assert ours == live


def test_checkpoint_reason_names_paths_like_the_live_validator():
    cp = _checkpoint()
    cp["working_context"]["open_questions"] = [
        {"text": "q", "trust": "inferred"}, {"text": None, "trust": "inferred"}]
    assert (field_table.checkpoint_reason(cp)
            == "working_context.open_questions[1]: text is not a str")


# ---- 3. generated normalizers reproduce today's dispositions exactly ----

@pytest.mark.parametrize("value,expected", [
    (5, 5),
    (1, 1),
    (10, 10),
    (0, 1),        # clamp low (the producer range is 1-10; serializer.py:678)
    (-3, 1),
    (11, 10),      # clamp high
    (9999, 10),
    (True, None),  # bool is an int subclass — dropped, never importance 1
    (False, None),
    ("5", None),
    (3.7, None),
    (None, None),
])
def test_normalize_importance_disposition(value, expected):
    item = _item(importance=value)
    field_table.normalize_field(item, "importance")
    assert item.get("importance", None) == expected
    if expected is None:
        assert "importance" not in item


@pytest.mark.parametrize("value,expected", [
    ("a scene", "a scene"),
    ("  padded  ", "padded"),
    ("x" * 600, "x" * 500),
    ("", None),
    ("   ", None),
    (7, None),
    (["s"], None),
    (None, None),
])
def test_normalize_scene_disposition(value, expected):
    item = _item(scene=value)
    field_table.normalize_field(item, "scene")
    assert item.get("scene", None) == expected
    if expected is None:
        assert "scene" not in item


def test_normalize_field_leaves_an_absent_field_absent():
    item = _item()
    field_table.normalize_field(item, "importance")
    field_table.normalize_field(item, "scene")
    assert item == _item()


def test_sanitize_importance_and_scene_still_behave_at_the_checkpoint_level():
    """The serializer entry points (now wired through the table) keep their
    contract: normalize every schema item in place, never raise."""
    cp = _checkpoint()
    cp["working_context"]["active_topic"]["importance"] = 12
    cp["working_context"]["open_questions"][0]["importance"] = True
    cp["epistemic_snapshot"]["uncertainties"][0]["scene"] = "  s  "
    serializer.sanitize_importance(cp)
    serializer.sanitize_scene(cp)
    assert cp["working_context"]["active_topic"]["importance"] == 10
    assert "importance" not in cp["working_context"]["open_questions"][0]
    assert cp["epistemic_snapshot"]["uncertainties"][0]["scene"] == "s"


# ---- 4. the table agrees with the code it describes ----

def test_scene_cap_matches_the_serializer_constant():
    row = field_table.rule("item", "scene")
    assert dict(row.constraints)["max_chars"] == serializer._SCENE_MAX_CHARS


def test_importance_bounds_are_the_producer_range():
    row = field_table.rule("item", "importance")
    c = dict(row.constraints)
    assert (c["min"], c["max"]) == (1, 10)


def test_trust_enum_matches_the_serializer_trust_classes():
    row = field_table.rule("item", "trust")
    assert set(dict(row.constraints)["enum"]) == serializer._TRUST_CLASSES


def test_stripped_envelope_keys_match_the_serializer_strip_list():
    stripped = {r.name for r in field_table.ENVELOPE_RULES
                if dict(r.constraints).get("stripped_at_serialize")}
    assert stripped == set(serializer._CODE_OWNED_KEYS)


def test_stripped_item_keys_match_the_serializer_strip_list():
    stripped = {r.name for r in field_table.ITEM_RULES
                if dict(r.constraints).get("stripped_at_serialize")}
    assert stripped == set(serializer._CODE_OWNED_ITEM_KEYS)


def test_every_schema_item_field_has_an_envelope_row():
    """schema.ITEM_FIELDS (#146) and the table must name the same sections."""
    rows = {r.name: r for r in field_table.ENVELOPE_RULES}
    for f in schema.ITEM_FIELDS:
        row = rows[f"{f.section}.{f.key}"]
        validate = dict(row.constraints).get("validate")
        if f.singleton:
            assert validate == "item-required"
        else:
            assert validate in ("item-list-required", "item-list-optional", None)


def test_no_duplicate_rows_and_scopes_are_coherent():
    for rules, scope in ((field_table.ENVELOPE_RULES, "envelope"),
                        (field_table.ITEM_RULES, "item")):
        names = [r.name for r in rules]
        assert len(names) == len(set(names))
        assert all(r.scope == scope for r in rules)
        assert all(r.owner in ("model", "code") for r in rules)
        assert all(r.disposition in ("reject", "clamp", "drop", "pass", "strip")
                   for r in rules)


# ---- 5. the published document ----

def test_schema_document_is_versioned_with_format_version():
    doc = field_table.schema_document(serializer.PROMPT_VERSION)
    assert doc["format_version"] == serializer.PROMPT_VERSION
    assert doc["document"] == "daimon-checkpoint-field-table"
    assert doc["document_version"] == field_table.SCHEMA_DOCUMENT_VERSION
    assert {row["field"] for row in doc["item"]} == {
        r.name for r in field_table.ITEM_RULES}
    assert {row["field"] for row in doc["envelope"]} == {
        r.name for r in field_table.ENVELOPE_RULES}
    for row in doc["envelope"] + doc["item"]:
        assert set(row) == {"field", "type", "optional", "nullable", "owner",
                            "disposition", "constraints", "notes"}


def test_render_document_is_deterministic_json():
    a = field_table.render_document("D-999")
    b = field_table.render_document("D-999")
    assert a == b
    assert a.endswith("\n")
    assert json.loads(a)["format_version"] == "D-999"


def test_published_artifact_matches_the_table():
    """docs/checkpoint-schema.json is generated, never hand-edited: a table
    change without `python -m daimon_briefing.field_table` fails here."""
    assert _ARTIFACT.is_file(), (
        "docs/checkpoint-schema.json is missing — regenerate it with "
        "`uv run python -m daimon_briefing.field_table` from plugin/")
    expected = field_table.render_document(serializer.PROMPT_VERSION)
    assert _ARTIFACT.read_text(encoding="utf-8") == expected


# ---- 6. normalizer equivalence holds on arbitrary shapes ----

def test_normalizers_and_sanitizers_agree_on_adversarial_checkpoints():
    cases = []
    for imp in (5, 0, 11, True, "5", None, 3.5):
        for scn in ("s", "", "  y  ", 9, None, "z" * 600):
            cases.append(_checkpoint(working_context={
                "active_topic": {"text": "t", "trust": "inferred",
                                 "importance": imp, "scene": scn},
                "open_questions": [], "recent_decisions": []}))
    for cp in cases:
        a, b = copy.deepcopy(cp), copy.deepcopy(cp)
        serializer.sanitize_importance(a)
        serializer.sanitize_scene(a)
        for item in serializer.iter_items(b):
            field_table.normalize_field(item, "importance")
            field_table.normalize_field(item, "scene")
        assert a == b
