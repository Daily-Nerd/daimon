"""Unit tests for the pure logic in replay.py (#976). Run:

  cd plugin && uv run --extra dev pytest ../research/experiments/trust-gate-cost-976/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugin"))

from daimon_briefing import briefing, scoring  # noqa: E402

from replay import (  # noqa: E402
    LID_KNEE,
    UNGATED_ARM,
    cap_crossings,
    fingerprint_files,
    is_lid_biting,
    is_truncation_exempting,
    item_key,
    positions_changed,
    rank_diff,
    should_flip,
    uncapped_weight,
    wilson,
)


# ---- the flip predicate is REUSED from #754, not re-implemented ----

def test_flip_predicate_is_the_same_object_as_the_754_runner():
    # Both experiments must share ONE predicate: a second definition that
    # drifts would make the two measurements answer different questions.
    # UNGATED_ARM is loaded BY PATH, so this is not the tautology a plain
    # `import replay` would give (both runners are named `replay`).
    expected = (Path(__file__).resolve().parents[1] / "ungated-arm"
                / "replay.py")
    assert Path(UNGATED_ARM.__file__) == expected
    assert should_flip is UNGATED_ARM.should_flip


def test_flip_predicate_still_targets_only_code_owned_downgrades():
    assert should_flip({"trust": "inferred", "quote_verified": False})
    assert should_flip({"trust": "inferred", "grounded": False})
    assert not should_flip({"trust": "inferred"})
    assert not should_flip({"trust": "verbatim", "quote_verified": True})


# ---- item identity (privacy: never the text itself) ----

def test_item_key_prefers_the_stamped_id():
    assert item_key({"id": "q-abc123", "text": "anything"}) == "q-abc123"


def test_item_key_hashes_the_text_when_no_id_is_stamped():
    k = item_key({"text": "a bench item with no stamped id"})
    assert k.startswith("h:")
    assert len(k) == 14
    assert "bench" not in k


def test_item_key_is_stable_and_distinguishes_items():
    a = {"text": "one"}
    assert item_key(a) == item_key({"text": "one"})
    assert item_key(a) != item_key({"text": "two"})


# ---- the uncapped (lid-removed) weight ----

def test_uncapped_weight_equals_effective_weight_below_the_knee():
    # The identity region: _soft_clip is the identity at or below K, so the
    # lid-removed product and the shipped ordering key must AGREE there. If
    # they disagree, the lid-biting diagnostic is measuring the wrong number.
    now = 1_700_000_000.0
    shapes = [
        # fresh, low importance, inferred: base 0.3 x recency 1.0 x decay
        ({"trust": "inferred", "importance": 3,
          "first_seen": "2023-11-14T22:13:20Z"}, "recent_decision"),
        # unstamped first_seen: neutral recency, no decay, no escalation
        ({"trust": "inferred", "importance": 5}, "strong_belief"),
        # old verbatim: recency 0.2 x floored decay, far under the inferred knee
        ({"trust": "verbatim", "importance": 6,
          "first_seen": "2022-11-14T22:13:20Z"}, "uncertainty"),
    ]
    for item, item_type in shapes:
        raw = uncapped_weight(item, item_type, now)
        assert raw <= LID_KNEE, (item_type, raw)
        assert raw == scoring.effective_weight(item, item_type, now)


def test_uncapped_weight_exceeds_effective_weight_above_the_knee():
    # A fresh, high-importance inferred item: the accumulation clears the
    # inferred lid, so the shipped key is clipped and the raw product is not.
    now = 1_700_000_000.0
    item = {"trust": "inferred", "importance": 10,
            "first_seen": "2023-11-14T22:13:20Z"}
    raw = uncapped_weight(item, "recent_decision", now)
    assert raw > LID_KNEE
    assert raw > scoring.effective_weight(item, "recent_decision", now)


def test_lid_knee_is_the_inferred_ceiling_soft_clip_knee():
    assert LID_KNEE == scoring.trust_ceiling("inferred") * (
        1.0 - scoring._SOFT_CLIP_DELTA)


# ---- lid-biting classification ----

def test_a_flip_below_the_knee_is_not_lid_biting():
    now = 1_700_000_000.0
    item = {"trust": "inferred", "importance": 2,
            "first_seen": "2023-11-14T22:13:20Z"}
    assert not is_lid_biting(item, "recent_decision", now)


def test_a_flip_above_the_knee_is_lid_biting():
    now = 1_700_000_000.0
    item = {"trust": "inferred", "importance": 10,
            "first_seen": "2023-11-14T22:13:20Z"}
    assert is_lid_biting(item, "recent_decision", now)


def test_lid_biting_reads_the_uncapped_product_not_the_stored_trust():
    # The item is asked about as it stands BEFORE the flip (trust=inferred);
    # the classification must not depend on having already rewritten trust.
    now = 1_700_000_000.0
    inferred = {"trust": "inferred", "importance": 9,
                "first_seen": "2023-11-14T22:13:20Z"}
    flipped = {**inferred, "trust": "verbatim"}
    assert is_lid_biting(inferred, "open_question", now) == is_lid_biting(
        flipped, "open_question", now)


# ---- the SECOND channel: render_plain's verbatim truncation exemption ----
#
# Found by the run, not predicted by it. Stage 1 of render_plain shortens
# oversized items in place EXCEPT verbatim ones (#30), so flipping an item to
# verbatim can make the render LONGER and cost a different item its place in
# the budget — a trust effect that never touches effective_weight.

def test_a_long_item_in_a_droppable_section_is_truncation_exempting():
    item = {"text": "x" * (briefing._ITEM_TRUNCATE_CHARS + 200)}
    assert is_truncation_exempting(item, "decisions")
    assert is_truncation_exempting(item, "beliefs")


def test_a_short_item_is_never_truncation_exempting():
    assert not is_truncation_exempting({"text": "short"}, "decisions")


def test_a_long_item_outside_the_drop_order_is_not_truncation_exempting():
    # `external` renders outside _DROP_ORDER, so stage 1 never rewrites it and
    # its trust class cannot change the rendered length.
    item = {"text": "x" * (briefing._ITEM_TRUNCATE_CHARS + 200)}
    assert not is_truncation_exempting(item, "external")


def test_truncation_exempting_asks_the_shipped_truncator():
    # The predicate must be "would render actually shorten this", not a length
    # comparison of the runner's own: truncate_preserving_sections may leave a
    # text over the cap alone, and a length proxy would then claim an effect
    # the render never has.
    cap = briefing._ITEM_TRUNCATE_CHARS
    for text in ("y" * (cap + 1), "y" * (cap * 3), "z" * (cap - 1)):
        item = {"text": text}
        shortens = briefing.truncate_preserving_sections(text, cap) != text
        assert is_truncation_exempting(item, "uncertainties") is shortens


# ---- ranked set / order diff ----

def test_rank_diff_reports_identity():
    d = rank_diff(["a", "b"], ["a", "b"])
    assert d["identical"] is True
    assert d["items_in"] == [] and d["items_out"] == []
    assert d["rank_deltas"] == {}


def test_rank_diff_reports_membership_changes():
    d = rank_diff(["a", "b"], ["b", "c"])
    assert d["identical"] is False
    assert d["items_in"] == ["c"]      # present in the ungated arm only
    assert d["items_out"] == ["a"]     # present in the gated arm only


def test_rank_diff_reports_per_item_rank_movement_for_shared_items():
    # "a" falls from rank 1 to rank 3, "c" climbs from rank 3 to rank 1.
    d = rank_diff(["a", "b", "c"], ["c", "b", "a"])
    assert d["rank_deltas"] == {"a": 2, "c": -2}
    assert "b" not in d["rank_deltas"]


def test_rank_diff_ignores_items_missing_from_one_arm_in_the_deltas():
    d = rank_diff(["a", "b"], ["b"])
    assert d["rank_deltas"] == {"b": -1}


# ---- section position diff ----

def test_positions_changed_counts_indices_that_differ():
    assert positions_changed(["a", "b", "c"], ["a", "c", "b"]) == 2


def test_positions_changed_is_zero_for_equal_sections():
    assert positions_changed(["a", "b"], ["a", "b"]) == 0
    assert positions_changed([], []) == 0


def test_positions_changed_counts_length_differences_as_changed():
    assert positions_changed(["a", "b"], ["a"]) == 1
    assert positions_changed(["a"], ["a", "b"]) == 1


# ---- cap crossings ----

def test_cap_crossings_is_empty_when_both_arms_render_the_same_items():
    c = cap_crossings({"a", "b"}, {"a", "b"})
    assert c == {"count": 0, "only_gated": [], "only_ungated": []}


def test_cap_crossings_names_the_items_each_arm_kept_alone():
    c = cap_crossings({"a", "b"}, {"b", "c"})
    assert c["count"] == 2
    assert c["only_gated"] == ["a"]
    assert c["only_ungated"] == ["c"]


def test_cap_crossings_sorts_so_the_record_is_byte_stable():
    c = cap_crossings({"z", "y", "x"}, set())
    assert c["only_gated"] == ["x", "y", "z"]


# ---- input fingerprint ----
#
# The local store is LIVE: other sessions write checkpoints into it while the
# replay runs, so "re-run it and the bytes match" is only a determinism claim
# once the inputs are pinned. The fingerprint is what pins them.

def test_fingerprint_is_a_sha256_hex_digest():
    fp = fingerprint_files([("a.json", "00" * 32)])
    assert len(fp) == 64
    assert set(fp) <= set("0123456789abcdef")


def test_fingerprint_ignores_the_order_files_were_read_in():
    a = ("a.json", "11" * 32)
    b = ("b.json", "22" * 32)
    assert fingerprint_files([a, b]) == fingerprint_files([b, a])


def test_fingerprint_changes_when_a_file_changes():
    assert (fingerprint_files([("a.json", "11" * 32)])
            != fingerprint_files([("a.json", "22" * 32)]))


def test_fingerprint_changes_when_a_file_is_renamed():
    assert (fingerprint_files([("a.json", "11" * 32)])
            != fingerprint_files([("b.json", "11" * 32)]))


def test_fingerprint_changes_when_a_file_is_added():
    one = [("a.json", "11" * 32)]
    assert fingerprint_files(one) != fingerprint_files(
        one + [("b.json", "22" * 32)])


def test_fingerprint_separates_the_name_from_the_digest():
    # Without a separator, ("ab", "c") and ("a", "bc") would collide and a
    # rename could hide behind an edit.
    assert (fingerprint_files([("ab", "c")])
            != fingerprint_files([("a", "bc")]))


# ---- Wilson score interval ----

def test_wilson_matches_published_value_for_one_of_one():
    lo, hi = wilson(1, 1)
    assert round(lo, 4) == 0.2065
    assert round(hi, 4) == 1.0


def test_wilson_matches_published_value_for_five_of_ten():
    lo, hi = wilson(5, 10)
    assert round(lo, 4) == 0.2366
    assert round(hi, 4) == 0.7634


def test_wilson_of_an_empty_denominator_is_the_whole_unit_interval():
    # No observations means no evidence, which is [0, 1] — never (0, 0),
    # which would read as a measured zero rate.
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_bounds_stay_inside_the_unit_interval():
    for k, n in ((0, 10), (10, 10), (1, 3)):
        lo, hi = wilson(k, n)
        assert 0.0 <= lo <= hi <= 1.0
