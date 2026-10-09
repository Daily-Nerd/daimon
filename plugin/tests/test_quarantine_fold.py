"""PR 13: the pure fold of ONE author's published quarantine rows.

Per file, per quarantine id, the last row wins by (order, rank, event_id,
line); a pair is active when any of its ids is. Rows here are plain dicts:
the fold never reads a file."""

from daimon_briefing import policy, trust

KEY = "0123456789abcdef"
OTHER = "fedcba9876543210"
TID = "tr-0123456789ab"
TID2 = "tr-ba9876543210"


def _row(state="active", *, kind="decision", key=KEY, tid=TID, order=1,
         event_id="e1", **extra):
    row = {"version": 1, "ts": "2026-10-09T12:00:00Z", "order": order,
           "event_id": event_id, "quarantine_id": tid, "kind": kind,
           "value_key": key, "state": state, "author": "ada"}
    row.update(extra)
    return row


def test_active_row_yields_the_pair():
    assert policy.fold_published_quarantines([_row()]) == {("decision", KEY)}


def test_no_rows_yield_nothing():
    assert policy.fold_published_quarantines([]) == frozenset()


def test_release_after_active_lifts_in_that_file():
    rows = [_row(order=1), _row("released", order=2, event_id="e2")]
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_active_after_release_reopens():
    rows = [_row(order=1), _row("released", order=2, event_id="e2"),
            _row(order=3, event_id="e3")]
    assert policy.fold_published_quarantines(rows) == {("decision", KEY)}


def test_file_order_does_not_matter_only_order_does():
    rows = [_row("released", order=2, event_id="e2"), _row(order=1)]
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_same_order_tie_ends_released():
    rows = [_row("released", order=5, event_id="a"),
            _row(order=5, event_id="b")]
    assert policy.fold_published_quarantines(rows) == frozenset()
    assert policy.fold_published_quarantines(rows[::-1]) == frozenset()


def test_rank_table_is_the_trust_ledgers():
    assert policy._EVENT_RANK == {
        name: trust._EVENT_RANK[name]
        for name in ("quarantined", "confirmed", "released")}
    assert policy._STATE_RANK["active"] < policy._STATE_RANK["released"]
    assert policy._STATE_RANK["released"] == trust._EVENT_RANK["released"]


def test_rows_without_order_fall_back_to_rank_then_line():
    rows = [{k: v for k, v in _row().items() if k not in ("order", "event_id")},
            {k: v for k, v in _row("released").items()
             if k not in ("order", "event_id")}]
    assert policy.fold_published_quarantines(rows) == frozenset()
    rows = [{k: v for k, v in _row("released").items()
             if k not in ("order", "event_id")}]
    rows.append({k: v for k, v in _row().items()
                 if k not in ("order", "event_id")})
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_two_ids_one_pair_release_of_one_keeps_the_pair():
    rows = [_row(tid=TID, order=1), _row(tid=TID2, order=2, event_id="e2"),
            _row("released", tid=TID, order=3, event_id="e3")]
    assert policy.fold_published_quarantines(rows) == {("decision", KEY)}


def test_two_ids_one_pair_both_released_lifts_it():
    rows = [_row(tid=TID, order=1), _row(tid=TID2, order=2, event_id="e2"),
            _row("released", tid=TID, order=3, event_id="e3"),
            _row("released", tid=TID2, order=4, event_id="e4")]
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_row_without_quarantine_id_keys_on_the_pair():
    a = _row(order=1)
    a.pop("quarantine_id")
    b = _row("released", order=2, event_id="e2")
    b.pop("quarantine_id")
    assert policy.fold_published_quarantines([a]) == {("decision", KEY)}
    assert policy.fold_published_quarantines([a, b]) == frozenset()


def test_a_malformed_quarantine_id_keys_on_the_pair():
    a = _row(tid="not-an-id", order=1)
    b = _row("released", tid="also-bad", order=2, event_id="e2")
    assert policy.fold_published_quarantines([a, b]) == frozenset()


def test_pairs_are_kind_scoped():
    rows = [_row(kind="decision"), _row(kind="belief", tid=TID2,
                                        order=2, event_id="e2")]
    assert policy.fold_published_quarantines(rows) == {
        ("decision", KEY), ("belief", KEY)}


def test_invalid_kind_state_or_key_is_ignored():
    bad = [_row(kind="nonsense"), _row(state="candidate"),
           _row(kind=["decision"]), _row(state={"x": 1}),
           _row(key="XYZ"), _row(key="abc"), _row(key=KEY.upper()),
           _row(key=7), "not a row", None, [], {"state": "active"}]
    assert policy.fold_published_quarantines(bad) == frozenset()


def test_value_key_length_bounds():
    assert policy.fold_published_quarantines([_row(key="a" * 8)]) == {
        ("decision", "a" * 8)}
    assert policy.fold_published_quarantines([_row(key="a" * 64)]) == {
        ("decision", "a" * 64)}
    assert policy.fold_published_quarantines([_row(key="a" * 65)]) == frozenset()


def test_minimal_d6_shape_still_folds():
    row = {"kind": "decision", "value_key": KEY, "quarantine_id": TID,
           "state": "active", "ts": "2026-10-09T12:00:00Z", "author": "ada"}
    assert policy.fold_published_quarantines([row]) == {("decision", KEY)}


def test_a_non_integer_order_reads_as_zero():
    rows = [_row(order="soon"), _row("released", order=0, event_id="e0")]
    assert policy.fold_published_quarantines(rows) == frozenset()


def test_an_invalid_row_does_not_lift_a_valid_claim():
    rows = [_row(order=1), _row("released", kind="nonsense", order=9)]
    assert policy.fold_published_quarantines(rows) == {("decision", KEY)}


# ---- fix round 1: nothing a teammate writes may raise out of the fold --------

import json  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN", "1e999",
                                     '"7"', "[7]", "true", "null", "{}"])
def test_a_non_finite_or_foreign_order_folds_as_zero(literal):
    """The rows come out of json.loads of independent bytes, the way a
    teammate's file does: `Infinity` and `1e999` are valid JSON to Python."""
    bad = json.loads(
        '{"kind":"decision","value_key":"%s","state":"active","order":%s}'
        % (KEY, literal))
    good = _row("released", order=-5, event_id="e2")
    # the odd row folds with order 0, so a release at order -5 is older
    assert policy.fold_published_quarantines([bad, good]) == {("decision", KEY)}
    # and it never raises, whatever else is in the file
    assert policy.fold_published_quarantines(
        [good, bad, _row(key=OTHER, tid=TID2)]) >= {("decision", OTHER)}


def test_an_infinite_order_never_outranks_a_real_release():
    forged = json.loads(
        '{"kind":"decision","value_key":"%s","state":"active",'
        '"quarantine_id":"%s","order":Infinity}' % (KEY, TID))
    rows = [_row(order=5), _row("released", order=9, event_id="e9"), forged]
    # order 0 sorts first: the real release at 9 still ends the id
    assert policy.fold_published_quarantines(rows) == frozenset()


@pytest.mark.parametrize("field,value", [
    ("ts", "\x1b]0;PWNED\x07"), ("ts", "yesterday"), ("event_id", "has space"),
    ("event_id", 7), ("author", "the text of the secret"),
    ("author", "x\x1b[2J"), ("version", "1"), ("version", True)])
def test_a_row_with_an_off_shape_field_is_ignored(field, value):
    assert policy.fold_published_quarantines(
        [_row(**{field: value})]) == frozenset()


def test_an_off_shape_release_does_not_lift_a_real_claim():
    rows = [_row(), _row("released", order=2, event_id="e2",
                         author="the text of the secret")]
    assert policy.fold_published_quarantines(rows) == {("decision", KEY)}


def test_the_strict_shape_a_publisher_may_write():
    ok = _row(event_id="a" * 32)
    assert policy.strict_published_row(ok)
    for change in ({"event_id": "e1"}, {"quarantine_id": "the secret"},
                   {"ts": "x"}, {"author": "a b"}, {"order": "1"},
                   {"order": float("inf")}, {"kind": "nope"},
                   {"value_key": "XYZ"}, {"state": "candidate"},
                   {"version": None}):
        assert not policy.strict_published_row({**ok, **change}), change
    for missing in ("order", "event_id", "ts", "author", "version",
                    "quarantine_id"):
        row = dict(ok)
        row.pop(missing)
        assert not policy.strict_published_row(row), missing
    assert not policy.strict_published_row("x")


def test_a_finite_float_order_sorts_by_its_integer_part():
    rows = [_row(order=5.9), _row("released", order=6.2, event_id="e2")]
    assert policy.fold_published_quarantines(rows) == frozenset()
    rows = [_row("released", order=5.9, event_id="e2"), _row(order=6.2)]
    assert policy.fold_published_quarantines(rows) == {("decision", KEY)}
