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
