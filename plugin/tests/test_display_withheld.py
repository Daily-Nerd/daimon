"""`display.withheld_marker` and `withheld_json` (#1132 PR 6a): how a
Withheld is shown, as text and as JSON, and nothing else."""

import json

import pytest

from daimon_briefing import display, view


def _w(reason, quarantine_id=None):
    return view.Withheld("o-aaaaaa", "decision", reason, quarantine_id,
                         "deadbeef" * 2)


def test_markers_name_the_reason_and_for_a_quarantine_its_record():
    assert display.withheld_marker(_w("quarantine", "tr-000000000001")) == (
        "[withheld: quarantine tr-000000000001]")
    assert display.withheld_marker(_w("forgotten")) == "[withheld: forgotten]"
    assert display.withheld_marker(_w("closed")) == (
        "[withheld: trust ledger unreadable]")


def test_a_quarantine_without_a_record_id_still_has_a_marker():
    assert display.withheld_marker(_w("quarantine")) == "[withheld: quarantine]"


def test_json_is_a_superset_of_the_state_withheld_encoding():
    assert display.withheld_json(_w("forgotten")) == {
        "state": "withheld", "reason": "forgotten"}
    assert display.withheld_json(_w("closed")) == {
        "state": "withheld", "reason": "closed"}
    assert display.withheld_json(_w("quarantine", "tr-000000000001")) == {
        "state": "withheld", "reason": "quarantine",
        "quarantine_id": "tr-000000000001"}


def test_json_and_markers_never_carry_the_value_key_or_the_item_id():
    for reason, qid in (("forgotten", None), ("closed", None),
                        ("quarantine", "tr-000000000001")):
        w = _w(reason, qid)
        blob = display.withheld_marker(w) + json.dumps(display.withheld_json(w))
        assert "deadbeef" not in blob and "o-aaaaaa" not in blob


def test_an_unknown_reason_is_refused():
    with pytest.raises(ValueError):
        display.withheld_marker(_w("surprise"))
    with pytest.raises(ValueError):
        display.withheld_json(_w("surprise"))
