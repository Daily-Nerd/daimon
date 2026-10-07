"""`view.classify` and `view.live` (#1132 PR 6a).

`live` twins the `briefing.withhold` resolution oracle (tests/test_briefing.py
#103 and #145): the same checkpoints, the same events, the same answers.
"""

import dataclasses
import itertools
from types import MappingProxyType

import pytest

from daimon_briefing import normalize, schema, view

SENTINEL = "SENTINEL value that must never surface"
KEY = normalize.content_key(SENTINEL)
FIELDS = list(schema.ITEM_FIELDS)


def _snap(**over):
    base = view.Snapshot.empty()
    fixed = {k: (MappingProxyType(v) if isinstance(v, dict) else v)
             for k, v in over.items()}
    return dataclasses.replace(base, **fixed)


def _item(value_field, text=SENTINEL, **extra):
    return {"id": "o-aaaaaa", value_field: text, **extra}


def _ids(f):
    return f"{f.section}.{f.key}"


@pytest.mark.parametrize("fld", FIELDS, ids=_ids)
@pytest.mark.parametrize("value_field", schema.VALUE_FIELDS)
def test_a_forgotten_value_is_withheld_in_every_field_and_column(
        fld, value_field):
    got = view.classify(fld, _item(value_field), _snap(
        forgotten=frozenset({KEY})))
    assert got == view.Withheld("o-aaaaaa", fld.kind, "forgotten", None, KEY)


@pytest.mark.parametrize("fld", FIELDS, ids=_ids)
@pytest.mark.parametrize("value_field", schema.VALUE_FIELDS)
def test_a_quarantine_of_this_kind_is_withheld_with_its_record(
        fld, value_field):
    snap = _snap(quarantined=frozenset({(fld.kind, KEY)}),
                 quarantine_ids={(fld.kind, KEY): "tr-000000000001"})
    got = view.classify(fld, _item(value_field), snap)
    assert got == view.Withheld("o-aaaaaa", fld.kind, "quarantine",
                                "tr-000000000001", KEY)


@pytest.mark.parametrize("fld", FIELDS, ids=_ids)
def test_a_quarantine_of_another_kind_leaves_the_item_visible(fld):
    other = next(f.kind for f in FIELDS if f.kind != fld.kind)
    snap = _snap(quarantined=frozenset({(other, KEY)}))
    item = _item("text")
    assert view.classify(fld, item, snap) == view.Visible(item)


@pytest.mark.parametrize("fld", FIELDS, ids=_ids)
@pytest.mark.parametrize("value_field", schema.VALUE_FIELDS)
def test_a_closed_snapshot_withholds_every_item(fld, value_field):
    got = view.classify(fld, _item(value_field, "anything at all"),
                        _snap(closed=True))
    assert got.reason == "closed" and got.quarantine_id is None


def test_a_closed_snapshot_withholds_even_an_item_with_no_value():
    fld = FIELDS[1]
    got = view.classify(fld, {"id": "o-aaaaaa"}, _snap(closed=True))
    assert got == view.Withheld("o-aaaaaa", fld.kind, "closed", None, "")


@pytest.mark.parametrize("flags", [
    {"closed": True, "forgotten": True, "quarantine": True},
    {"closed": False, "forgotten": True, "quarantine": True},
    {"closed": True, "forgotten": False, "quarantine": True},
    {"closed": True, "forgotten": True, "quarantine": False},
])
def test_precedence_is_closed_then_forgotten_then_quarantine(flags):
    fld = FIELDS[2]
    snap = _snap(
        closed=flags["closed"],
        forgotten=frozenset({KEY}) if flags["forgotten"] else frozenset(),
        quarantined=(frozenset({(fld.kind, KEY)})
                     if flags["quarantine"] else frozenset()))
    got = view.classify(fld, _item("text"), snap)
    want = ("closed" if flags["closed"]
            else "forgotten" if flags["forgotten"] else "quarantine")
    assert got.reason == want


def test_a_forgotten_and_quarantined_value_is_not_announced_as_quarantined():
    fld = FIELDS[2]
    got = view.classify(fld, _item("text"), _snap(
        forgotten=frozenset({KEY}), quarantined=frozenset({(fld.kind, KEY)}),
        quarantine_ids={(fld.kind, KEY): "tr-000000000001"}))
    assert got.reason == "forgotten" and got.quarantine_id is None


def test_matching_is_by_canonical_value_not_by_spelling():
    fld = FIELDS[2]
    snap = _snap(forgotten=frozenset({KEY}))
    assert isinstance(view.classify(
        fld, _item("text", "  " + SENTINEL.upper() + "  "), snap), view.Withheld)


def test_a_bare_string_contradiction_is_matched_as_its_own_text():
    fld = FIELDS[-1]
    assert fld.key == "contradictions_flagged"
    got = view.classify(fld, SENTINEL, _snap(forgotten=frozenset({KEY})))
    assert got == view.Withheld(None, fld.kind, "forgotten", None, KEY)
    assert view.classify(fld, "something else", _snap()) == view.Visible(
        "something else")


def test_entries_that_are_neither_dict_nor_string_are_visible_unless_closed():
    fld = FIELDS[-1]
    assert view.classify(fld, 42, _snap()) == view.Visible(42)
    assert view.classify(fld, 42, _snap(closed=True)).reason == "closed"


def test_the_singleton_is_classified_like_any_item():
    fld = FIELDS[0]
    assert fld.singleton
    got = view.classify(fld, {"text": SENTINEL}, _snap(
        forgotten=frozenset({KEY})))
    assert got.reason == "forgotten" and got.item_id is None


def test_withheld_has_no_text_quote_or_scene_attribute():
    names = {f.name for f in dataclasses.fields(view.Withheld)}
    assert names == {"item_id", "kind", "reason", "quarantine_id", "value_key"}
    got = view.classify(FIELDS[2], _item("text"), _snap(
        forgotten=frozenset({KEY})))
    for attr in schema.VALUE_FIELDS + ("item",):
        assert not hasattr(got, attr)
    assert SENTINEL not in repr(got)


def test_withheld_is_frozen():
    got = view.classify(FIELDS[2], _item("text"), _snap(
        forgotten=frozenset({KEY})))
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.reason = "closed"  # type: ignore[misc]


# ---- prose_withheld -------------------------------------------------------


def test_prose_withheld_matches_the_whole_value_against_both_sets():
    snap = _snap(forgotten=frozenset({KEY}))
    assert view.prose_withheld(SENTINEL, snap) is True
    assert view.prose_withheld("  " + SENTINEL.lower(), snap) is True
    assert view.prose_withheld("something else", snap) is False
    assert view.prose_withheld("", snap) is False
    assert view.prose_withheld(None, snap) is False
    quarantined = _snap(quarantined=frozenset({("belief", KEY)}))
    assert view.prose_withheld(SENTINEL, quarantined) is True


def test_a_closed_snapshot_withholds_all_prose():
    assert view.prose_withheld("", _snap(closed=True)) is True
    assert view.prose_withheld("hello", _snap(closed=True)) is True


# ---- live: twins of the briefing.withhold oracle --------------------------


def _evt(ref, status="resolved", text=""):
    e = {"ts": "2026-07-07T00:00:00Z", "kind": "resolution",
         "item_ref": ref, "status": status}
    if text:
        e["item_text"] = text
    return e


def _res(**events):
    return _snap(resolutions=events)


def test_live_by_exact_id():  # test_withhold_by_exact_id
    snap = _res(**{"o-aaa": _evt("o-aaa")})
    assert view.live({"text": "is the gateway stable", "id": "o-aaa"},
                     snap) is False
    assert view.live({"text": "does carry hold", "id": "o-bbb"}, snap) is True


def test_a_reopen_event_keeps_the_item_live():  # test_reopen_event_does_not_withhold
    snap = _res(**{"o-aaa": _evt("o-aaa", status="reopened")})
    assert view.live({"text": "x y z", "id": "o-aaa"}, snap) is True


def test_a_legacy_idless_item_is_closed_by_fuzzy_item_text():
    # test_legacy_idless_item_withheld_by_item_text_fuzzy: "o-old01" is not an
    # id-shaped ref (not hex), so its text feeds the fuzzy pool.
    snap = _res(**{"o-old01": _evt(
        "o-old01", text="release pipeline manual approval gate awaiting")})
    item = {"text": "release pipeline approval step still awaiting manual gate"}
    assert view.live(item, snap) is False


def test_an_id_bearing_item_is_never_fuzzy_closed():
    snap = _res(**{"o-old01": _evt(
        "o-old01", text="release pipeline manual approval gate awaiting")})
    legacy = _res(**{"pipeline gate loop": _evt(
        "pipeline gate loop",
        text="release pipeline manual approval gate awaiting")})
    item = {"text": "release pipeline manual approval gate awaiting",
            "id": "o-live1"}
    assert view.live(item, snap) is True
    assert view.live(item, legacy) is True     # exact text, id-bearing: never fuzzy


def test_a_live_idless_item_survives_an_id_bearing_resolved_overlap():  # #145
    snap = _res(**{"o-3f2a9c": _evt(
        "o-3f2a9c", text="release pipeline manual approval gate awaiting")})
    item = {"text": "release pipeline approval step still awaiting manual gate"}
    assert view.live(item, snap) is True


def test_an_idless_resolution_still_fuzzy_closes_among_id_bearing_ones():  # #145
    snap = _res(**{
        "pipeline gate loop": _evt(
            "pipeline gate loop",
            text="release pipeline manual approval gate awaiting"),
        "o-9bd41e": _evt("o-9bd41e", text="gateway retry budget confirmed stable")})
    item = {"text": "release pipeline approval step still awaiting manual gate"}
    assert view.live(item, snap) is False


def test_exact_id_suppression_is_unchanged_by_the_fuzzy_pool_restriction():  # #145
    snap = _res(**{"o-3f2a9c": _evt(
        "o-3f2a9c", text="release pipeline manual approval gate awaiting")})
    item = {"text": "release pipeline manual approval gate awaiting",
            "id": "o-3f2a9c"}
    assert view.live(item, snap) is False


def test_with_no_resolutions_everything_is_live():  # test_no_resolved_events_...
    assert view.live({"text": "x", "id": "o-a"}, view.Snapshot.empty()) is True


def test_non_dict_entries_and_textless_items_are_live():
    snap = _res(**{"pipeline gate loop": _evt("pipeline gate loop",
                                              text="some closed loop text")})
    assert view.live("a bare string", snap) is True
    assert view.live({"id": ""}, snap) is True
    assert view.live({"text": ""}, snap) is True


def test_candidate_and_claim_statuses_are_not_resolutions():
    snap = _res(**{"o-aaa": _evt("o-aaa", status="supersede-candidate:o-bbb")})
    assert view.live({"text": "x", "id": "o-aaa"}, snap) is True


def test_live_agrees_with_briefing_withhold_over_generated_cases():
    """The twin of the oracle over a small cross product: the same
    checkpoint through `briefing.withhold` and `view.live` drops the same
    items."""
    from daimon_briefing import briefing
    texts = ["release pipeline approval step awaiting manual gate",
             "gateway retry budget confirmed stable",
             "unrelated question about caching"]
    ids = [None, "o-aaaaaa", "o-bbbbbb"]
    refs = ["pipeline gate loop", "o-aaaaaa", "o-cccccc"]
    statuses = ["resolved", "reopened", "supersede-candidate:o-dddddd"]
    for text, item_id, ref, status in itertools.product(
            texts, ids, refs, statuses):
        item = {"text": text}
        if item_id:
            item["id"] = item_id
        events = {ref: _evt(ref, status=status, text=texts[0])}
        cp = {"working_context": {"open_questions": [item]}}
        _out, withheld, _cand = briefing.withhold(cp, events)
        assert view.live(item, _res(**events)) is (not withheld), (
            text, item_id, ref, status)
