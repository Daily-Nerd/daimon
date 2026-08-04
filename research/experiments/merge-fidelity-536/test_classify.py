"""Tests for the #536 frozen classification rubric.

Every rule here restates the pre-registration comment on issue #536
verbatim; if a test wants a different behaviour, the protocol — not the
test — wins, and the mismatch is a protocol failure to report.
"""
import classify


def U(text, chunk=0, trust="inferred"):
    return classify.UnionItem(text=text, chunk=chunk, trust=trust)


def N(text, trust="inferred"):
    return classify.NativeItem(text=text, trust=trust)


class TestNormalize:
    def test_casefold_and_whitespace_collapse(self):
        assert classify.normalize("  Foo\tBAR\n baz ") == "foo bar baz"

    def test_empty(self):
        assert classify.normalize("") == ""


class TestSurvived:
    def test_exact_normalized_match_is_survived(self):
        r = classify.classify(
            native=[N("Merge kept THIS  item")],
            union=[U("merge kept this item")],
            prev_verbatim=[])
        assert r.survived == 1 and r.reworded == 0

    def test_one_to_one_no_double_spend(self):
        # two identical natives, one union ancestor: only one survives
        r = classify.classify(
            native=[N("same text"), N("same text")],
            union=[U("same text")],
            prev_verbatim=[])
        assert r.survived == 1
        assert r.emitted_new == 1


class TestReworded:
    def test_fuzzy_above_threshold_is_reworded(self):
        r = classify.classify(
            native=[N("the deadline bug makes resample unrecoverable")],
            union=[U("deadline bug leaves the resample unrecoverable by construction")],
            prev_verbatim=[])
        assert r.reworded == 1 and r.survived == 0

    def test_below_threshold_is_not_reworded(self):
        r = classify.classify(
            native=[N("completely unrelated statement about apples")],
            union=[U("kubernetes volume detached during failover")],
            prev_verbatim=[])
        assert r.reworded == 0
        assert r.emitted_new == 1
        assert r.true_lost == 1


class TestFreezeExplained:
    def test_prev_verbatim_match_is_freeze_explained_not_new(self):
        r = classify.classify(
            native=[N("frozen wording from the carry path")],
            union=[U("something else entirely different topic")],
            prev_verbatim=["frozen wording from the carry path"])
        assert r.freeze_explained == 1
        assert r.emitted_new == 0

    def test_freeze_pass_runs_after_union_passes(self):
        # a native matching BOTH union and prev-verbatim counts as survived
        r = classify.classify(
            native=[N("appears in both places")],
            union=[U("appears in both places")],
            prev_verbatim=["appears in both places"])
        assert r.survived == 1 and r.freeze_explained == 0


class TestEmittedNewAndTrueLost:
    def test_no_ancestor_is_emitted_new_with_trust_breakdown(self):
        r = classify.classify(
            native=[N("invented by the merge", trust="verbatim")],
            union=[],
            prev_verbatim=[])
        assert r.emitted_new == 1
        assert r.emitted_new_by_trust == {"verbatim": 1}

    def test_union_without_descendant_is_true_lost_with_trust_breakdown(self):
        r = classify.classify(
            native=[],
            union=[U("dropped on the floor", trust="verbatim")],
            prev_verbatim=[])
        assert r.true_lost == 1
        assert r.true_lost_by_trust == {"verbatim": 1}

    def test_deterministic_under_input_order(self):
        native = [N("alpha item one"), N("beta item two")]
        union = [U("beta item two"), U("alpha item one")]
        a = classify.classify(native=native, union=union, prev_verbatim=[])
        b = classify.classify(native=list(reversed(native)),
                              union=list(reversed(union)), prev_verbatim=[])
        assert (a.survived, a.reworded, a.true_lost) == \
               (b.survived, b.reworded, b.true_lost) == (2, 0, 0)


class TestContainment:
    def test_sixty_char_slice_containment_flagged(self):
        long = "x" * 30 + " the load bearing sixty character slice payload " + "y" * 30
        r = classify.classify(
            native=[N(long + " plus a merged-in tail from another item making fuzzy fail entirely different words appended here to push ratio down " + "z" * 200)],
            union=[U(long)],
            prev_verbatim=[])
        assert r.containment_flags >= 1

    def test_short_items_do_not_flag_containment(self):
        r = classify.classify(
            native=[N("short")],
            union=[U("other")],
            prev_verbatim=[])
        assert r.containment_flags == 0


class TestCrossChunkTwins:
    def test_twin_in_different_chunk_counts(self):
        r = classify.classify(
            native=[],
            union=[U("the same discovery stated twice", chunk=0),
                   U("the same discovery stated twice again", chunk=1)],
            prev_verbatim=[])
        assert r.twin_items == 2

    def test_twin_in_same_chunk_does_not_count(self):
        r = classify.classify(
            native=[],
            union=[U("the same discovery stated twice", chunk=0),
                   U("the same discovery stated twice again", chunk=0)],
            prev_verbatim=[])
        assert r.twin_items == 0


class TestWilson:
    def test_wilson_interval_known_value(self):
        lo, hi = classify.wilson(3, 30)
        assert 0.03 < lo < 0.05
        assert 0.25 < hi < 0.27

    def test_wilson_zero_denominator(self):
        assert classify.wilson(0, 0) == (0.0, 0.0)
