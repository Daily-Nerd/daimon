"""Serialize→brief E2E over the shared item-field schema (#146).

Builds a checkpoint populating EVERY item-bearing field schema.ITEM_FIELDS
declares, runs it through the real post-extraction pipeline
(serializer.sanitize_importance -> store.write_checkpoint -> store.read_latest
-> briefing.render), and asserts each item comes out whole: first_seen stamped,
importance sanitized, id present (list items), text + trust tag in the brief.

Descriptor-driven on purpose: a field added to schema.ITEM_FIELDS is covered
here with no test edit — and a consumer that drops a declared list fails this
test instead of shipping the drift silently."""

import re

from daimon_briefing import briefing, schema, serializer, store

_CREATED = "2026-07-08T00:00:00Z"
_PROJECT = "/tmp/daimon-e2e-project"


def _probe_text(field: schema.ItemField) -> str:
    return f"probe item for {field.key}"


def _item(field: schema.ItemField) -> dict:
    return {
        "text": _probe_text(field),
        "trust": "inferred",
        # Out of range on purpose: sanitize_importance must clamp it to 10.
        # A consumer that skips this field leaves 99 behind — the assertion
        # below catches exactly that.
        "importance": 99,
    }


def _full_checkpoint() -> dict:
    cp = {"session_id": "S-e2e", "created": _CREATED}
    for f in schema.ITEM_FIELDS:
        block = cp.setdefault(f.section, {})
        block[f.key] = _item(f) if f.singleton else [_item(f)]
    return cp


def test_descriptor_covers_known_drift_field():
    # The field whose omission from iter_items motivated #146 must be declared.
    assert ("epistemic_snapshot", "contradictions_flagged") in schema.ITEM_LISTS


def test_every_schema_field_survives_serialize_to_brief(tmp_checkpoint_dir):
    cp = _full_checkpoint()
    assert serializer.validate(cp), "fixture must pass the serializer gate"

    # The real pipeline order: sanitize (serialize step), then the store write
    # (redaction, id stamping, first_seen stamping), then read + render.
    serializer.sanitize_importance(cp)
    store.write_checkpoint("S-e2e", cp, project_dir=_PROJECT)

    stored = store.read_latest(project_dir=_PROJECT)
    assert stored is not None

    for f in schema.ITEM_FIELDS:
        block = stored.get(f.section) or {}
        items = [block.get(f.key)] if f.singleton else block.get(f.key)
        assert items, f"{f.key}: dropped between write and read"
        for item in items:
            assert isinstance(item, dict), f"{f.key}: item shape mangled"
            assert item.get("first_seen") == _CREATED, (
                f"{f.key}: first_seen missing or wrong ({item.get('first_seen')!r})")
            assert item.get("importance") == 10, (
                f"{f.key}: importance not sanitized ({item.get('importance')!r})")
            if not f.singleton:
                assert item.get("id"), f"{f.key}: id not stamped"

    brief = briefing.render(stored)
    assert brief is not None

    lines = brief.splitlines()
    for f in schema.ITEM_FIELDS:
        text = _probe_text(f)
        assert text in brief, f"{f.key}: item missing from the brief"
        if f.singleton:
            continue  # active_topic renders as a header line, no trust tag
        line = next(ln for ln in lines if text in ln)
        assert re.match(r"- \[~ inferred\]", line), (
            f"{f.key}: item rendered without its trust tag: {line!r}")
