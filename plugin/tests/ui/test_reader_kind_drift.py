"""daimon_ui/reader.py reads checkpoints without importing daimon, so it keeps
its own section tables. This test (which may import schema; reader.py may not)
pins them equal to `schema.ITEM_FIELDS` so they can never drift (#1132 PR 4).
"""
import json

import pytest

from daimon_briefing import schema
from daimon_ui import reader

LIST_FIELDS = [(f.section, f.key, f.kind) for f in schema.ITEM_FIELDS
               if not f.singleton]


def _field_of(sections, section_key):
    """The ITEM_FIELDS row a viewer section reads. `verify_first` is the
    synthesized external-state slice of open_questions and has no row of its
    own in `_SECTIONS`."""
    if section_key == "verify_first":
        return ("working_context", "open_questions")
    for ui_key, _label, container, cp_key in sections:
        if ui_key == section_key:
            return (container, cp_key)
    return None


def _drift(sections, section_kind):
    """What differs between the viewer's tables and ITEM_FIELDS."""
    problems = []
    if {(c, k) for _u, _l, c, k in sections} != set(schema.ITEM_LISTS):
        problems.append("sections do not cover the list fields")
    kinds = {(f.section, f.key): f.kind for f in schema.ITEM_FIELDS}
    for section_key, kind in section_kind.items():
        field = _field_of(sections, section_key)
        if field is None or kind != kinds.get(field):
            problems.append(f"{section_key} -> {kind}")
    return problems


def test_the_viewer_tables_match_the_item_fields():
    assert _drift(reader._SECTIONS, reader._SECTION_KIND) == []


def test_the_drift_check_catches_a_wrong_kind():
    wrong = dict(reader._SECTION_KIND, decisions="belief")
    assert _drift(reader._SECTIONS, wrong) == ["decisions -> belief"]


def test_the_drift_check_catches_a_missing_section():
    assert _drift(reader._SECTIONS[1:], reader._SECTION_KIND)


def test_every_section_the_viewer_builds_has_a_kind():
    built = {"verify_first"} | {u for u, _l, _c, _k in reader._SECTIONS}
    assert set(reader._SECTION_KIND) == built


@pytest.mark.parametrize("section,key,kind", LIST_FIELDS,
                         ids=[k for _s, k, _kd in LIST_FIELDS])
def test_list_buckets_counts_every_list_field(tmp_path, section, key, kind):
    bucket = tmp_path / "-proj"
    bucket.mkdir()
    checkpoint = {"created": "2026-08-06T10:00:00Z",
                  "working_context": {}, "epistemic_snapshot": {}}
    checkpoint[section][key] = [{"text": "one"}]
    (bucket / "latest.json").write_text(json.dumps(checkpoint))
    [got] = reader.list_buckets(tmp_path, "-proj")
    assert got["item_count"] == 1


def test_list_buckets_does_not_count_the_singleton(tmp_path):
    bucket = tmp_path / "-proj"
    bucket.mkdir()
    (bucket / "latest.json").write_text(json.dumps({
        "created": "2026-08-06T10:00:00Z",
        "working_context": {"active_topic": {"text": "t"}},
        "epistemic_snapshot": {}}))
    [got] = reader.list_buckets(tmp_path, "-proj")
    assert got["item_count"] == 0
