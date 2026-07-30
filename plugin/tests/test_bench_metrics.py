"""Unit tests for the benchmark metrics (#267). Pure functions — no LLM, no I/O."""

from tests.bench import metrics


def _res(session_id, text="some item", rank=1.0):
    """A recall.search result row, trimmed to the fields metrics reads."""
    return {"session_id": session_id, "text": text, "rank": rank}


class TestRankedSessions:
    def test_dedup_preserves_first_occurrence_order(self):
        rows = [_res("s3"), _res("s1"), _res("s3"), _res("s2"), _res("s1")]
        assert metrics.ranked_sessions(rows) == ["s3", "s1", "s2"]

    def test_empty_results(self):
        assert metrics.ranked_sessions([]) == []

    def test_rows_without_session_id_are_dropped(self):
        rows = [_res("s1"), {"text": "no session"}, {"session_id": None}]
        assert metrics.ranked_sessions(rows) == ["s1"]


class TestRecallAtK:
    def test_full_coverage(self):
        assert metrics.recall_at_k(["s1", "s2", "s3"], {"s1", "s2"}, 5) == 1.0

    def test_partial_coverage(self):
        # one of two gold sessions inside the window
        assert metrics.recall_at_k(["s1", "x", "y"], {"s1", "s2"}, 5) == 0.5

    def test_gold_outside_window_scores_zero(self):
        assert metrics.recall_at_k(["a", "b", "c", "d", "e", "s1"], {"s1"}, 5) == 0.0

    def test_empty_gold_returns_none(self):
        # abstention questions carry no evidence session — excluded, not zero
        assert metrics.recall_at_k(["s1"], set(), 5) is None


class TestHitAtK:
    def test_hit_when_any_gold_in_window(self):
        assert metrics.hit_at_k(["x", "s1", "y"], {"s1"}, 5) is True

    def test_miss_when_gold_outside_window(self):
        assert metrics.hit_at_k(["a", "b", "c", "d", "e", "s1"], {"s1"}, 5) is False

    def test_empty_gold_returns_none(self):
        assert metrics.hit_at_k(["s1"], set(), 5) is None


class TestReciprocalRank:
    def test_first_position(self):
        assert metrics.reciprocal_rank(["s1", "s2"], {"s1"}) == 1.0

    def test_third_position(self):
        assert metrics.reciprocal_rank(["a", "b", "s2"], {"s2"}) == 1 / 3

    def test_earliest_gold_wins(self):
        # two gold sessions -> rank of the first one encountered
        assert metrics.reciprocal_rank(["a", "s2", "s1"], {"s1", "s2"}) == 0.5

    def test_no_gold_retrieved(self):
        assert metrics.reciprocal_rank(["a", "b"], {"s1"}) == 0.0

    def test_empty_gold_returns_none(self):
        assert metrics.reciprocal_rank(["a"], set()) is None


class TestTokenEstimate:
    def test_char_over_four_heuristic(self):
        assert metrics.estimate_tokens("a" * 40) == 10

    def test_empty_string(self):
        assert metrics.estimate_tokens("") == 0

    def test_injected_tokens_sums_topk_texts(self):
        rows = [_res("s1", "a" * 40), _res("s2", "b" * 40), _res("s3", "c" * 40)]
        # only the top-2 texts count toward the injected budget
        assert metrics.injected_tokens(rows, 2) == 20


class TestAggregate:
    def test_means_skip_none_and_count_buckets(self):
        per_q = [
            {"recall_at_5": 1.0, "hit_at_5": True, "mrr": 1.0,
             "injected_tokens": 100, "abstention": False},
            {"recall_at_5": 0.0, "hit_at_5": False, "mrr": 0.0,
             "injected_tokens": 50, "abstention": False},
            # abstention row: retrieval metrics are None, excluded from means
            {"recall_at_5": None, "hit_at_5": None, "mrr": None,
             "injected_tokens": 0, "abstention": True},
        ]
        agg = metrics.aggregate(per_q, k=5)
        assert agg["questions_total"] == 3
        assert agg["questions_scored"] == 2
        assert agg["questions_abstention"] == 1
        assert agg["recall_at_5"] == 0.5
        assert agg["hit_at_5"] == 0.5
        assert agg["mrr"] == 0.5
        # tokens averaged over ALL questions (efficiency of the whole run)
        assert agg["avg_injected_tokens"] == 50.0

    def test_no_scored_questions(self):
        agg = metrics.aggregate([], k=5)
        assert agg["questions_scored"] == 0
        assert agg["recall_at_5"] is None
        assert agg["mrr"] is None


# ---- #405: forbidden-hit dimension ------------------------------------------


class TestAssembledBriefText:
    """The load-bearing seam: forbidden material is scored against the text the
    briefing actually DELIVERS, not the raw retriever output. The delivered brief
    is the top-k SURVIVING rows' text+quote — windowed and withhold-filtered."""

    def test_joins_topk_delivered_fields(self):
        rows = [_res("s1", "alpha topic"), _res("s2", "beta topic"),
                _res("s3", "gamma topic")]
        text = metrics.assembled_brief_text(rows, k=2)
        assert "alpha topic" in text and "beta topic" in text
        # gamma is retrieved but outside the delivered top-k window
        assert "gamma" not in text

    def test_includes_quote_payload(self):
        # the verbatim quote reaches the prompt too — a prime leak surface
        rows = [{"session_id": "s1", "text": "a decision", "quote": "we chose postgres"}]
        text = metrics.assembled_brief_text(rows, k=5)
        assert "we chose postgres" in text

    def test_withholds_superseded_rows(self):
        # recall still RETURNS a resolved/superseded row (ranked down, flagged);
        # assembly DROPS it — scoring the raw retriever output would be a false leak
        rows = [
            {"session_id": "s1", "text": "live decision"},
            {"session_id": "s2", "text": "stale secret", "superseded_by": "resolved"},
        ]
        text = metrics.assembled_brief_text(rows, k=5)
        assert "live decision" in text
        assert "stale secret" not in text

    def test_withheld_rows_do_not_consume_window_slots(self):
        rows = [
            {"session_id": "s1", "text": "one", "superseded_by": "x-abc123"},
            {"session_id": "s2", "text": "two"},
            {"session_id": "s3", "text": "three"},
        ]
        text = metrics.assembled_brief_text(rows, k=2)
        assert "two" in text and "three" in text
        assert "one" not in text

    def test_empty_results(self):
        assert metrics.assembled_brief_text([], k=5) == ""


class TestForbiddenHitsFound:
    def test_case_insensitive_substring(self):
        assert metrics.forbidden_hits_found("The API_KEY is abc", ["api_key"]) == ["api_key"]

    def test_returns_only_matched_preserving_order(self):
        found = metrics.forbidden_hits_found("alpha beta", ["gamma", "alpha"])
        assert found == ["alpha"]

    def test_dedups_and_ignores_blank_entries(self):
        found = metrics.forbidden_hits_found("alpha alpha", ["alpha", "  ", "alpha"])
        assert found == ["alpha"]

    def test_empty_forbidden_list(self):
        assert metrics.forbidden_hits_found("anything at all", []) == []


class TestScoredRecall:
    """score = max(0, base - matched/len(forbidden)). Leakage is disqualifying,
    subtracted from the case's OWN score (not averaged away), floored at zero."""

    def test_no_forbidden_returns_base_unchanged(self):
        assert metrics.scored_recall(1.0, 0, 0) == 1.0

    def test_clean_case_returns_base(self):
        assert metrics.scored_recall(1.0, 0, 2) == 1.0

    def test_partial_leak_subtracts_proportionally(self):
        assert metrics.scored_recall(1.0, 1, 2) == 0.5

    def test_full_leak_floors_case_to_zero(self):
        assert metrics.scored_recall(1.0, 2, 2) == 0.0

    def test_never_goes_negative(self):
        assert metrics.scored_recall(0.5, 2, 2) == 0.0

    def test_none_base_stays_none(self):
        assert metrics.scored_recall(None, 1, 2) is None


class TestAggregateForbidden:
    def test_rate_and_penalized_recall(self):
        per_q = [
            {"recall_at_5": 1.0, "hit_at_5": True, "mrr": 1.0, "injected_tokens": 10,
             "abstention": False, "forbidden_total": 2, "forbidden_matched": 0,
             "forbidden_hit": False, "recall_at_5_penalized": 1.0},
            {"recall_at_5": 1.0, "hit_at_5": True, "mrr": 1.0, "injected_tokens": 10,
             "abstention": False, "forbidden_total": 2, "forbidden_matched": 2,
             "forbidden_hit": True, "recall_at_5_penalized": 0.0},
            # no forbidden material defined -> excluded from the leak rate
            {"recall_at_5": 0.5, "hit_at_5": True, "mrr": 0.5, "injected_tokens": 10,
             "abstention": False, "forbidden_total": 0, "forbidden_matched": 0,
             "forbidden_hit": None, "recall_at_5_penalized": 0.5},
        ]
        agg = metrics.aggregate(per_q, k=5)
        assert agg["questions_with_forbidden"] == 2
        assert agg["forbidden_hit_rate"] == 0.5
        # penalized recall averaged over ALL scored questions
        assert agg["recall_at_5_penalized"] == 0.5

    def test_no_forbidden_questions_rate_is_none(self):
        agg = metrics.aggregate([], k=5)
        assert agg["questions_with_forbidden"] == 0
        assert agg["forbidden_hit_rate"] is None
