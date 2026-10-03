"""Unit tests for the pure logic in replay.py (#1129). Run:

  cd plugin && uv run --extra dev pytest ../research/experiments/quote-span-1129/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugin"))

from replay import (  # noqa: E402
    add_counts,
    aggregate,
    diff_kept,
    item_key,
    summarize,
    trust_class,
    whole_quote,
)


def test_item_key_hashes_id_and_text_in_separate_domains():
    assert item_key({"id": "x"}) != item_key({"text": "x"})
    assert len(item_key({"id": "d-1"})) == 16
    assert "d-1" not in item_key({"id": "d-1"})


def test_whole_quote_is_the_stored_quote_stripped():
    assert whole_quote({"quote": "  words  "}, "text") == "words"
    assert whole_quote({"quote": None}, "text") == ""


def test_trust_class_buckets_unknown_values():
    assert trust_class({"trust": "verbatim"}) == "verbatim"
    assert trust_class({"trust": "weird"}) == "other"
    assert trust_class({}) == "other"


def test_diff_kept_reports_gained_and_lost_by_class():
    a = {"k1": "verbatim", "k2": "inferred"}
    b = {"k2": "inferred", "k3": "verbatim", "k4": "other"}
    d = diff_kept(a, b)
    assert d["gained"] == {"verbatim": 1, "other": 1}
    assert d["lost"] == {"verbatim": 1}


def test_summarize_handles_empty_and_values():
    assert summarize([]) == {"n": 0, "median": 0, "max": 0}
    assert summarize([1, 5, 3]) == {"n": 3, "median": 3, "max": 5}


def test_add_counts_accumulates():
    total = {"verbatim": 1}
    add_counts(total, {"verbatim": 2, "other": 1})
    assert total == {"verbatim": 3, "other": 1}


def test_aggregate_counts_changed_briefings():
    rows = [
        {"checkpoint": "a", "horizon_days": 1, "changed": True,
         "gained": {"verbatim": 1}, "lost": {}, "bytes_saved_unbounded": 400,
         "over_budget_a": True, "quoted": 2, "hidden": 1, "capped": 1},
        {"checkpoint": "b", "horizon_days": 1, "changed": False,
         "gained": {}, "lost": {}, "bytes_saved_unbounded": 0,
         "over_budget_a": False, "quoted": 0, "hidden": 0, "capped": 0},
        {"checkpoint": "c", "skipped": "no created stamp"},
    ]
    out = aggregate(rows)
    assert out["briefings_measured"] == 2
    assert out["briefings_changed"] == 1
    assert out["briefings_changed_by_horizon"] == {"1": 1, "30": 0}
    assert out["skipped"] == 1
    assert out["items_gained_by_class"] == {"verbatim": 1}
    assert out["bytes_saved_unbounded"]["max"] == 400
    assert out["bytes_saved_unbounded_when_quoted"]["n"] == 1
