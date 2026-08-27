"""Unit tests for the pure logic in replay.py (#754). Run manually:

  cd plugin && uv run python -m pytest ../research/experiments/ungated-arm/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import classify_gold, should_flip  # noqa: E402


def test_flip_targets_quote_verification_downgrade():
    assert should_flip({"trust": "inferred", "quote_verified": False})


def test_flip_targets_grounding_downgrade():
    assert should_flip({"trust": "inferred", "grounded": False})


def test_natively_inferred_items_never_flip():
    # No code-owned downgrade marker: the model emitted inferred directly.
    assert not should_flip({"trust": "inferred"})
    assert not should_flip({"trust": "inferred", "quote_verified": True})


def test_verbatim_items_never_flip():
    # A verified verbatim item must not be touched, whatever its markers.
    assert not should_flip({"trust": "verbatim", "quote_verified": True})
    assert not should_flip({"trust": "verbatim"})


def test_classify_gold_orders_by_visibility():
    ranked = ["s1", "s2", "s3", "s4", "s5", "s6"]
    assert classify_gold("s1", ranked, "indexed") == "hit_top5"
    assert classify_gold("s6", ranked, "indexed") == "indexed_deep"
    assert classify_gold("s9", ranked, "indexed") == "indexed_unretrieved"
    assert classify_gold("s9", ranked, "too_short") == "absent_too_short"
    assert classify_gold("s9", ranked, "missing") == "absent_missing"
