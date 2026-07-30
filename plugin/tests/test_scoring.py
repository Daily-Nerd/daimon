"""Effective-weight scoring (#78): importance x recency x type decay x overdue
escalation. Pure + deterministic — `now` injected everywhere."""

import time as _time

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
