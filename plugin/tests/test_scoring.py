"""Effective-weight scoring (#78): importance x recency x type decay x overdue
escalation. Pure + deterministic — `now` injected everywhere."""

import time as _time

import pytest

from daimon_briefing import scoring

_NOW = 1_800_000_000.0  # fixed epoch; all ages derived from here


def _iso(days_ago):
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_NOW - days_ago * 86400))


def _item(days_ago=None, importance=None):
    it = {"text": "x", "trust": "inferred"}
    if days_ago is not None:
        it["first_seen"] = _iso(days_ago)
    if importance is not None:
        it["importance"] = importance
    return it


def test_recency_tiers():
    assert scoring.recency_weight(0.5) == 1.0
    assert scoring.recency_weight(5) == 0.9
    assert scoring.recency_weight(20) == 0.7
    assert scoring.recency_weight(60) == 0.4
    assert scoring.recency_weight(200) == 0.2


def test_fresh_beats_stale_same_importance():
    fresh = scoring.effective_weight(_item(0, 5), "recent_decision", _NOW)
    stale = scoring.effective_weight(_item(60, 5), "recent_decision", _NOW)
    assert fresh > stale


def test_importance_separates_same_age():
    hi = scoring.effective_weight(_item(3, 9), "open_question", _NOW)
    lo = scoring.effective_weight(_item(3, 2), "open_question", _NOW)
    assert hi > lo


def test_missing_first_seen_is_neutral_not_crash():
    w = scoring.effective_weight(_item(None, 5), "open_question", _NOW)
    assert 0 < w < 1
    # neutral sits between fresh and ancient
    assert w < scoring.effective_weight(_item(0, 5), "open_question", _NOW)
    assert w > scoring.effective_weight(_item(365, 5), "open_question", _NOW)


def test_missing_importance_defaults_mid_scale():
    default = scoring.effective_weight(_item(1), "open_question", _NOW)
    assert scoring.effective_weight(_item(1, 4), "open_question", _NOW) < default
    assert scoring.effective_weight(_item(1, 6), "open_question", _NOW) > default


def test_overdue_open_question_outranks_same_age_decision():
    # The #78 point: at 30d, an unanswered open loop must SURFACE relative to a
    # 30d-old decision (which sinks) — escalation vs plain decay.
    q = scoring.effective_weight(_item(30, 5), "open_question", _NOW)
    d = scoring.effective_weight(_item(30, 5), "recent_decision", _NOW)
    assert q > d


def test_type_decay_never_hits_zero():
    w = scoring.effective_weight(_item(3650, 10), "recent_decision", _NOW)
    assert w > 0


def test_unknown_type_gets_default_rules():
    w = scoring.effective_weight(_item(3, 5), "no-such-type", _NOW)
    assert 0 < w <= 1


def test_deterministic():
    a = scoring.effective_weight(_item(12, 7), "uncertainty", _NOW)
    b = scoring.effective_weight(_item(12, 7), "uncertainty", _NOW)
    assert a == b


def test_future_stamp_does_not_outrank_fresh():
    # Teammate clock skew (#31 item 8): a stamp hours in the future must not
    # take max recency — it gets NEUTRAL weight, below a genuinely fresh item.
    future = scoring.effective_weight(_item(-2, 5), "recent_decision", _NOW)
    fresh = scoring.effective_weight(_item(0, 5), "recent_decision", _NOW)
    neutral = scoring.effective_weight(_item(None, 5), "recent_decision", _NOW)
    assert future < fresh
    assert future == neutral


def test_small_clock_skew_tolerated_as_fresh():
    # Seconds-level skew between machines is normal — treat as age 0, not lies.
    skewed = _item(None, 5)
    skewed["first_seen"] = _time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_NOW + 60))  # 60s in the future
    w = scoring.effective_weight(skewed, "recent_decision", _NOW)
    assert w == scoring.effective_weight(_item(0, 5), "recent_decision", _NOW)


# ---- #408: trust class as an authority CEILING ----


def test_trust_ceiling_table_is_monotone_verbatim_on_top():
    # The table is a monotone lid: verbatim earns the top band, and every
    # lower-trust class (inferred, untagged/unknown) sits strictly below it.
    v = scoring.trust_ceiling("verbatim")
    i = scoring.trust_ceiling("inferred")
    untagged = scoring.trust_ceiling(None)
    unknown = scoring.trust_ceiling("not-a-real-class")
    assert v > i                    # verbatim strictly above inferred
    assert i >= untagged >= 0.0     # non-increasing down the ladder
    assert untagged == unknown      # absent tag and unknown tag share the lid


def test_no_input_lifts_effective_weight_above_trust_ceiling():
    """Architecture guard (#408): effective_weight is the ONE authority
    function every read path (briefing order, carry survival, recall rank)
    routes through. Drive it with an adversarial cross-product — max
    importance, every scoring type, ages that maximize overdue escalation,
    future and missing stamps, out-of-range importances — and assert the
    output NEVER crosses the record's trust ceiling.

    This asserts the cap is UNCONDITIONAL: there is no combination of
    importance / recency / overdue escalation that promotes a lower-trust
    item into a higher band. It is a test that the promoting path does not
    EXIST, not a test that some path behaves."""
    types = list(scoring.TYPE_RULES) + ["no-such-type"]
    ages = [None, -30, -1, 0, 1, 7, 14, 15, 21, 30, 45, 60, 90, 200, 3650]
    importances = [None, -5, 0, 1, 5, 9, 10, 11, 999]  # incl. out-of-range
    for trust in ("verbatim", "inferred", None, "bogus-class", ""):
        ceiling = scoring.trust_ceiling(trust)
        for t in types:
            for age in ages:
                for imp in importances:
                    it: dict = {}
                    if trust is not None:
                        it["trust"] = trust
                    if age is not None:
                        it["first_seen"] = _iso(age)
                    if imp is not None:
                        it["importance"] = imp
                    w = scoring.effective_weight(it, t, _NOW)
                    assert w <= ceiling + 1e-9, (trust, t, age, imp, w, ceiling)


def test_inferred_cannot_reach_verbatim_band():
    # The strongest an inferred item can get — fresh, max importance — still
    # sits strictly below the authority a verbatim item of the SAME shape
    # reaches. Recency / importance / recall frequency cannot close this gap:
    # the ceiling is a property of the record, not the score.
    shape = {"first_seen": _iso(0), "importance": 10}
    inferred = scoring.effective_weight(
        {**shape, "trust": "inferred"}, "recent_decision", _NOW)
    verbatim = scoring.effective_weight(
        {**shape, "trust": "verbatim"}, "recent_decision", _NOW)
    assert inferred < verbatim
    assert inferred <= scoring.trust_ceiling("inferred")


def test_overdue_inferred_open_question_still_capped():
    # Escalation is the sharpest promotion vector — an overdue open loop grows
    # its own weight back. It must NOT let an inferred item cross its ceiling,
    # however far overdue: escalation counters decay, it never defeats trust.
    for age in (15, 21, 30, 45, 90, 200):
        w = scoring.effective_weight(
            {"trust": "inferred", "first_seen": _iso(age), "importance": 10},
            "open_question", _NOW)
        assert w <= scoring.trust_ceiling("inferred") + 1e-9


def test_verbatim_keeps_full_escalation_range():
    # The ceiling must not clip a verified verbatim item: its lid is the
    # escalation cap, so a verbatim open loop keeps every bit of overdue boost.
    v_ceiling = scoring.trust_ceiling("verbatim")
    assert v_ceiling >= scoring._ESCALATION_CAP


# ---- #488: the ceiling must BOUND the order, not erase it ----


def test_clamped_items_keep_distinct_weights_by_importance():
    # #408's min() caps correctly and destroys injectivity on [C, inf): every
    # fresh inferred item at importance 8/9/10 evaluated to exactly 0.700, so
    # among the HIGHEST-importance items in a briefing the tiebreak fell to
    # whatever order the serializer happened to emit (briefing._by_weight is a
    # stable sort). The lid was only ever asked to bound the score.
    ws = [scoring.effective_weight(
        {"trust": "inferred", "first_seen": _iso(0), "importance": imp},
        "open_question", _NOW) for imp in (7, 8, 9, 10)]
    assert len(set(ws)) == len(ws), f"importance ordering collapsed: {ws}"
    assert ws == sorted(ws), f"not monotone in importance: {ws}"


def test_soft_clip_never_attains_the_ceiling():
    # Strictly tighter than min(), which ATTAINS the lid. Drive the same
    # adversarial cross-product shape as the #408 guard and assert strict
    # inequality, not <=.
    ceiling = scoring.trust_ceiling("inferred")
    for imp in (8, 9, 10):
        for age in (0, 1, 2):
            w = scoring.effective_weight(
                {"trust": "inferred", "first_seen": _iso(age),
                 "importance": imp}, "open_question", _NOW)
            assert w < ceiling, (imp, age, w, ceiling)


def test_soft_clip_is_a_noop_below_the_knee():
    # The change must touch ONLY the degenerate region. Anything whose raw
    # accumulation lands below the knee scores exactly what it scored before.
    ceiling = scoring.trust_ceiling("inferred")
    knee = ceiling * (1 - scoring._SOFT_CLIP_DELTA)
    for age, imp in ((30, 5), (60, 3), (90, 7), (200, 2)):
        item = {"trust": "inferred", "first_seen": _iso(age),
                "importance": imp}
        w = scoring.effective_weight(item, "recent_decision", _NOW)
        if w < knee:                      # only assert on the untouched region
            raw = (imp / 10.0) * scoring.recency_weight(age) * scoring._type_decay(
                age, scoring.TYPE_RULES["recent_decision"])
            assert abs(w - raw) < 1e-12, (age, imp, w, raw)


def test_soft_clip_preserves_the_cross_class_band():
    # #408's load-bearing property: no inferred item reaches the band a
    # verbatim item of the same shape occupies. The soft clip must not leak
    # across it while it is busy separating ties.
    shape = {"first_seen": _iso(0), "importance": 10}
    inferred = scoring.effective_weight(
        {**shape, "trust": "inferred"}, "open_question", _NOW)
    verbatim = scoring.effective_weight(
        {**shape, "trust": "verbatim"}, "open_question", _NOW)
    assert inferred < scoring.trust_ceiling("inferred") < verbatim


def test_soft_clip_at_a_zero_ceiling_silences_without_a_special_case():
    # A trust class with no authority is expressible in the table (nothing
    # forbids a 0.0 lid). The closed form handles it: the knee collapses to 0,
    # the gap term vanishes, and every weight maps to 0.0 — so no guard branch
    # is needed, and none exists to rot untested.
    for w in (0.0, 0.5, 1.0, 3.0):
        assert scoring._soft_clip(w, 0.0) == 0.0, w
    # and the live table has no non-positive lid, which is why the above is a
    # statement about the formula rather than about production behavior.
    assert all(v > 0 for v in scoring.TRUST_CEILING.values())
    assert scoring._DEFAULT_CEILING > 0


# ---- #840 / q-6cd17264d205: the ranking must be interrogable ----------------
#
# effective_weight decides ordering on three read paths and was never exposed,
# so a consumer wanting to answer "why is this ranked here" had to reimplement
# scoring.py and drift from it. daimon-ui cannot import daimon, so
# reimplementation was its only option.
#
# Persistence was rejected in the acceptance: the weight decays, so a value
# stored at write time is stale by the time anything reads it, and a stale
# ranking number is worse than none because it looks authoritative. The shape
# is recompute at read time and publish the result WITH the inputs, because a
# number a reader cannot recompute is still a ranking they have to trust blind.


def test_explain_publishes_inputs_that_reproduce_the_weight():
    # Frozen reference, computed by hand rather than from the code under test:
    # importance 8 -> base 0.8; 10 days old -> recency tier 0.7; recent_decision
    # decay 0.02/day -> 1 - 10*0.02 = 0.8; no escalation for that type; verbatim
    # ceiling 3.0 leaves the knee at 2.7, so nothing clips. 0.8*0.7*0.8 = 0.448.
    item = {"importance": 8, "first_seen": _iso(10), "trust": "verbatim"}
    got = scoring.explain(item, "recent_decision", _NOW)

    assert got["effective_weight"] == pytest.approx(0.448)
    assert got["computed_at"] == _NOW
    assert got["inputs"] == {
        "importance": 8,
        "importance_source": "item",
        "trust": "verbatim",
        "trust_ceiling": 3.0,
        "first_seen": _iso(10),
        "age_days": pytest.approx(10.0),
    }
    assert got["factors"]["base"] == pytest.approx(0.8)
    assert got["factors"]["recency"] == pytest.approx(0.7)
    assert got["factors"]["type_decay"] == pytest.approx(0.8)
    assert got["factors"]["overdue_boost"] == 1.0
    assert got["factors"]["raw"] == pytest.approx(0.448)


def test_explain_factors_multiply_to_the_weight_it_publishes():
    # The one invariant that keeps the explanation honest: raw is the product
    # of the published factors, and the published weight is that product under
    # the trust lid. Asserted across shapes that exercise every branch,
    # including the clipped one where raw and weight legitimately differ.
    #
    # This is deliberately NOT `explain(...)["effective_weight"] ==
    # effective_weight(...)`. explain delegates the authoritative number to
    # effective_weight, so that comparison is a tautology that would keep
    # passing if the factors drifted away from the math entirely.
    cases = [
        ({"importance": 8, "first_seen": _iso(10), "trust": "verbatim"}, "recent_decision"),
        ({"importance": 10, "first_seen": _iso(0), "trust": "inferred"}, "open_question"),
        ({"importance": 9, "first_seen": _iso(200), "trust": "verbatim"}, "open_question"),
        ({"importance": 3, "first_seen": _iso(45), "trust": None}, "strong_belief"),
        ({"trust": "inferred"}, "active_topic"),
    ]
    for item, item_type in cases:
        got = scoring.explain(item, item_type, _NOW)
        f = got["factors"]
        product = f["base"] * f["recency"] * f["type_decay"] * f["overdue_boost"]
        assert f["raw"] == pytest.approx(product), (item, item_type)
        assert got["effective_weight"] == pytest.approx(
            scoring._soft_clip(f["raw"], got["inputs"]["trust_ceiling"])), (item, item_type)


def test_explain_names_a_substituted_importance_as_a_default():
    # An unscored item is not an importance-5 item, and a consumer recomputing
    # from a published 5 without knowing it was substituted would report a
    # score the record never carried.
    got = scoring.explain({"first_seen": _iso(1)}, "recent_decision", _NOW)
    assert got["inputs"]["importance"] == scoring._DEFAULT_IMPORTANCE
    assert got["inputs"]["importance_source"] == "default"


def test_explain_reports_an_unstamped_item_as_neutral_rather_than_fresh():
    # No first_seen means neutral recency, not maximum. The published age is
    # null rather than 0, because "unknown age" and "brand new" are different
    # facts and collapsing them is how a consumer reinvents the bug.
    got = scoring.explain({"importance": 6, "trust": "inferred"}, "recent_decision", _NOW)
    assert got["inputs"]["age_days"] is None
    assert got["inputs"]["first_seen"] is None
    assert got["factors"]["recency"] == pytest.approx(scoring._NEUTRAL_RECENCY)
    assert got["factors"]["type_decay"] == 1.0
    assert got["factors"]["overdue_boost"] == 1.0


def test_explain_reports_the_rules_actually_applied_for_an_unknown_type():
    # effective_weight falls back to _DEFAULT_RULES for a type it does not
    # know. Publishing the requested type alone would describe a computation
    # that did not happen.
    got = scoring.explain({"importance": 5}, "not-a-real-type", _NOW)
    assert got["item_type"] == "not-a-real-type"
    assert got["rules"] == "recent_decision"


def test_explain_shows_the_overdue_boost_that_escalation_applied():
    # open_question is the one auto-escalating type; an item past its expected
    # lifespan gets a boost, and a consumer cannot reproduce the number
    # without it.
    got = scoring.explain(
        {"importance": 5, "first_seen": _iso(60), "trust": "verbatim"},
        "open_question", _NOW)
    assert got["factors"]["overdue_boost"] > 1.0
