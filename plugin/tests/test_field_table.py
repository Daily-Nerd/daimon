"""#827: the declarative field table is the single source of truth.

Three guarantees, each pinned here:

1. The generated validator reproduces the ratified contract — verdicts AND
   reason strings — across a matrix that exercises every predicate branch.
   The contract lives in this file as FROZEN REFERENCE implementations
   (transcribed from the pre-#827 serializer), NOT as calls through the live
   code path: serializer now delegates to field_table, so comparing the two
   live functions would be f(x) == f(x) (the scar-0042 tautology class).
   Exactly one wiring test proves the delegation itself.
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


# ---- The frozen contract (reference implementations) ----
#
# LITERAL transcriptions of serializer._item_reason / validation_reason /
# sanitize_importance+sanitize_scene as they stood before #827 wired them
# through the table. They deliberately never call the live code. ONE ratified
# divergence from the pre-#827 bytes (#828 review, finding kept as an
# improvement): trust membership is checked against a TUPLE, not a set, so an
# unhashable trust value (e.g. a list) rejects cleanly as an unknown trust
# class instead of raising TypeError out of serialize_strict.

def _reference_item_reason(item):
    if not isinstance(item, dict):
        return "not a dict"
    if "text" not in item or "trust" not in item:
        return "missing text or trust"
    if not isinstance(item["text"], str):
        return "text is not a str"
    if item["trust"] not in ("verbatim", "inferred"):
        return "trust is not a known trust class"
    if item["trust"] == "verbatim":
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            return "trust=verbatim item has no quote"
    because = item.get("because")
    if because is not None and not isinstance(because, str):
        return "because is not a str"
    anchor = item.get("anchored_to")
    if anchor is not None:
        if not isinstance(anchor, dict):
            return "anchored_to is not a dict"
        if not all(
            isinstance(anchor.get(k), str) and anchor.get(k)
            for k in ("file", "symbol", "body_hash")
        ):
            return "anchored_to is missing file, symbol, or body_hash"
    return None


def _reference_checkpoint_reason(checkpoint):
    if not isinstance(checkpoint, dict):
        return "checkpoint is not a dict"
    if "session_id" not in checkpoint:
        return "missing session_id"
    wc = checkpoint.get("working_context")
    es = checkpoint.get("epistemic_snapshot")
    if not isinstance(wc, dict):
        return "working_context is not a dict"
    if not isinstance(es, dict):
        return "epistemic_snapshot is not a dict"
    if "active_topic" not in wc:
        return "missing working_context.active_topic"
    for key in ("open_questions", "recent_decisions"):
        items = wc.get(key)
        if not isinstance(items, list):
            return f"working_context.{key} is not a list"
        for i, item in enumerate(items):
            reason = _reference_item_reason(item)
            if reason is not None:
                return f"working_context.{key}[{i}]: {reason}"
    reason = _reference_item_reason(wc["active_topic"])
    if reason is not None:
        return f"working_context.active_topic: {reason}"
    for key in ("strong_beliefs", "uncertainties"):
        items = es.get(key, [])
        if not isinstance(items, list):
            return f"epistemic_snapshot.{key} is not a list"
        for i, item in enumerate(items):
            reason = _reference_item_reason(item)
            if reason is not None:
                return f"epistemic_snapshot.{key}[{i}]: {reason}"
    return None


def _reference_sanitize(item):
    """importance + scene dispositions, per-item, as the pre-#827 serializer
    checkpoint walks applied them."""
    if "importance" in item:
        v = item["importance"]
        if isinstance(v, int) and not isinstance(v, bool):
            item["importance"] = min(10, max(1, v))
        else:
            del item["importance"]
    if "scene" in item:
        v = item["scene"]
        if isinstance(v, str) and v.strip():
            item["scene"] = v.strip()[:500]
        else:
            del item["scene"]


# ---- 1. generated item validator == the frozen contract ----

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
    _item(trust=["verbatim"]),               # unhashable: clean reject (#828)
    _item(trust={"v": 1}),
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
def test_item_reason_matches_the_frozen_contract(item):
    assert field_table.item_reason(item) == _reference_item_reason(item)


def test_item_reason_strings_are_the_ratified_predicate_names():
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


def test_unhashable_trust_rejects_cleanly_instead_of_raising():
    """#828 review, ratified: pre-#827 an unhashable trust raised TypeError
    out of serialize_strict; the table engine rejects it as an unknown trust
    class, which routes it into the ordinary #118 resample instead."""
    assert (field_table.item_reason(_item(trust=["verbatim"]))
            == "trust is not a known trust class")


# ---- 2. generated checkpoint validator == the frozen contract ----

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
    _set(("working_context", "active_topic"), None),   # explicit null rejects
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
def test_checkpoint_reason_matches_the_frozen_contract(mutate):
    ours = field_table.checkpoint_reason(mutate(_checkpoint()))
    ref = _reference_checkpoint_reason(mutate(_checkpoint()))
    assert ours == ref


def test_checkpoint_reason_names_paths_like_the_ratified_contract():
    cp = _checkpoint()
    cp["working_context"]["open_questions"] = [
        {"text": "q", "trust": "inferred"}, {"text": None, "trust": "inferred"}]
    assert (field_table.checkpoint_reason(cp)
            == "working_context.open_questions[1]: text is not a str")


def test_explicit_null_active_topic_rejects():
    """The published row says nullable=false; the validator must agree —
    contract accuracy is the point of #827 (#828 review)."""
    cp = _checkpoint()
    cp["working_context"]["active_topic"] = None
    assert (field_table.checkpoint_reason(cp)
            == "working_context.active_topic: not a dict")
    assert field_table.rule(
        "envelope", "working_context.active_topic").nullable is False


# ---- 3. the one wiring test: serializer delegates to the table ----

def test_serializer_delegates_to_the_field_table(monkeypatch):
    """The matrix above pins the ENGINE against the frozen contract without
    touching the live path; this single test pins the WIRING — that the
    serializer entry points actually route through the table engine."""
    normalized = []
    monkeypatch.setattr(field_table, "item_reason", lambda item: "ITEM-SENTINEL")
    monkeypatch.setattr(field_table, "checkpoint_reason", lambda cp: "CP-SENTINEL")
    monkeypatch.setattr(field_table, "normalize_field",
                        lambda item, name: normalized.append(name))
    assert serializer._item_reason(_item()) == "ITEM-SENTINEL"
    assert serializer.validation_reason(_checkpoint()) == "CP-SENTINEL"
    cp = _checkpoint()
    serializer.sanitize_importance(cp)
    serializer.sanitize_scene(cp)
    assert "importance" in normalized
    assert "scene" in normalized


# ---- 4. generated normalizers reproduce today's dispositions exactly ----

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


def test_normalize_field_raises_on_a_row_it_does_not_implement():
    """#828 review: a silent no-op on an unimplemented (type, disposition)
    combination would leave a malformed value in place while reading as
    normalization at the call site — the drop of source_message_ids lives in
    sanitize_source_ids (it needs transcript context), never here."""
    with pytest.raises(ValueError):
        field_table.normalize_field(_item(source_message_ids=13),
                                    "source_message_ids")
    with pytest.raises(ValueError):
        field_table.normalize_field(_item(), "text")


def test_normalizers_match_the_frozen_contract_on_adversarial_checkpoints():
    cases = []
    for imp in (5, 0, 11, True, "5", None, 3.5):
        for scn in ("s", "", "  y  ", 9, None, "z" * 600):
            cases.append(_checkpoint(working_context={
                "active_topic": {"text": "t", "trust": "inferred",
                                 "importance": imp, "scene": scn},
                "open_questions": [], "recent_decisions": []}))
    for cp in cases:
        a, b = copy.deepcopy(cp), copy.deepcopy(cp)
        for item in serializer.iter_items(a):
            _reference_sanitize(item)
        for item in serializer.iter_items(b):
            field_table.normalize_field(item, "importance")
            field_table.normalize_field(item, "scene")
        assert a == b


# ---- 5. the table agrees with the code it describes ----

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


def test_every_schema_item_field_has_a_walked_envelope_row():
    """schema.ITEM_FIELDS (#146) and the table must name the same sections,
    and every item-bearing section must actually be WALKED by the validator —
    a list row without validate= would silently skip its items (the #134
    incident shape). Free-form-by-design sections are exempt only via an
    explicit disposition of pass (#828 review)."""
    rows = {r.name: r for r in field_table.ENVELOPE_RULES}
    for f in schema.ITEM_FIELDS:
        row = rows[f"{f.section}.{f.key}"]
        validate = dict(row.constraints).get("validate")
        if f.singleton:
            assert validate == "item-required"
        elif row.disposition == "pass":
            assert validate is None   # free-form by design (contradictions)
        else:
            assert validate in ("item-list-required", "item-list-optional")


def test_no_duplicate_rows_and_scopes_are_coherent():
    for rules, scope in ((field_table.ENVELOPE_RULES, "envelope"),
                        (field_table.ITEM_RULES, "item")):
        names = [r.name for r in rules]
        assert len(names) == len(set(names))
        assert all(r.scope == scope for r in rules)
        assert all(r.owner in ("model", "code") for r in rules)
        assert all(r.disposition in ("reject", "clamp", "drop", "pass", "strip")
                   for r in rules)


def test_engine_coverage_guard_accepts_the_real_tables():
    assert field_table.engine_coverage_errors() == []


def test_engine_coverage_guard_names_rows_the_engine_cannot_run():
    """#828 review: the validator dispatches on constraint keys, not the type
    column, so a shape the engine never anticipated must fail at import —
    not fall through to the wrong branch inside a serialize."""
    bad_item = field_table.FieldRule(
        "item", "count", "integer", True, False, "model", "reject",
        (("reject_reason", "count is bad"),), "")
    errors = field_table.engine_coverage_errors(
        item_rules=(bad_item,), envelope_rules=())
    assert errors and "count" in errors[0]

    bad_envelope = field_table.FieldRule(
        "envelope", "extra", "object", True, False, "model", "reject",
        (("validate", "frobnicate"),), "")
    errors = field_table.engine_coverage_errors(
        item_rules=(), envelope_rules=(bad_envelope,))
    assert errors and "frobnicate" in errors[0]

    unclamped = field_table.FieldRule(
        "item", "blob", "object", True, False, "model", "clamp", (), "")
    errors = field_table.engine_coverage_errors(
        item_rules=(unclamped,), envelope_rules=())
    assert errors and "blob" in errors[0]


# ---- 6. the published document ----

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


def test_schema_document_glossaries_cover_every_constraint_key():
    """#828 review: a column-driven consumer must be able to resolve every
    constraint key it meets (required_if_eq especially — the quote row's
    reject is conditional) from the document itself."""
    doc = field_table.schema_document(serializer.PROMPT_VERSION)
    used = {key
            for rows in (field_table.ENVELOPE_RULES, field_table.ITEM_RULES)
            for r in rows for key, _ in r.constraints}
    assert used <= set(doc["constraint_semantics"])
    assert set(doc["columns"]) >= {"type", "optional", "nullable", "owner",
                                   "disposition", "constraints"}


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
