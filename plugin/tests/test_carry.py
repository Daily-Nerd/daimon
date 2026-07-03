"""Deterministic carry (#33 Phase 2): merge() folds the previous checkpoint's
unresolved items into the new one by code. Pure function — all clock/config
injected."""
import copy

from daimon_briefing import carry

NOW = 1_760_000_000.0  # arbitrary fixed epoch


def _iso(days_before_now):
    import datetime as dt
    t = dt.datetime.fromtimestamp(NOW - days_before_now * 86400, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cp(sid, created_days_ago=0, questions=(), decisions=(), uncertainties=()):
    return {
        "session_id": sid,
        "created": _iso(created_days_ago),
        "working_context": {
            "active_topic": {"text": f"topic {sid}", "trust": "inferred"},
            "open_questions": list(questions),
            "recent_decisions": list(decisions),
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": list(uncertainties),
            "contradictions_flagged": [],
        },
    }


def _item(text, imp=7, days=2, **kw):
    return {"text": text, "trust": "inferred", "importance": imp,
            "first_seen": _iso(days), **kw}


def test_carries_unresolved_question_with_provenance():
    prev = _cp("S-prev", 1, questions=[_item("zephyr ledger drop unresolved")])
    new = _cp("S-new")
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["text"] == "zephyr ledger drop unresolved"
    assert qs[0]["carried_from"] == "S-prev"
    assert qs[0]["first_seen"] == _iso(2)  # verbatim, stamps intact


def test_origin_provenance_survives_chains():
    prev = _cp("S-prev", 1, questions=[
        _item("zephyr ledger drop unresolved", carried_from="S-origin")])
    out = carry.merge(_cp("S-new"), prev, NOW)
    assert out["working_context"]["open_questions"][0]["carried_from"] == "S-origin"


def test_weight_floor_drops_stale_low_importance():
    # imp-2 decision 60 days old: 0.2 * 0.4 * 0.1 = 0.008 < 0.05 -> falls off.
    # imp-7 open question 60 days old: escalated well above floor -> carries.
    prev = _cp("S-prev", 1,
               decisions=[_item("plimsol trivia", imp=2, days=60)],
               questions=[_item("quorint loop still open", imp=7, days=60)])
    out = carry.merge(_cp("S-new"), prev, NOW)
    assert out["working_context"]["recent_decisions"] == []
    assert len(out["working_context"]["open_questions"]) == 1


def test_beliefs_and_topic_never_carry():
    prev = _cp("S-prev", 1)
    prev["epistemic_snapshot"]["strong_beliefs"] = [_item("belief text")]
    out = carry.merge(_cp("S-new"), prev, NOW)
    assert out["epistemic_snapshot"]["strong_beliefs"] == []
    assert out["working_context"]["active_topic"]["text"] == "topic S-new"


def test_idempotent_exact_text_guard():
    prev = _cp("S-prev", 1, questions=[_item("zephyr ledger drop unresolved")])
    once = carry.merge(_cp("S-new"), prev, NOW)
    twice = carry.merge(once, prev, NOW)
    assert len(twice["working_context"]["open_questions"]) == 1


def test_anachronism_guard_no_backward_merge():
    prev = _cp("S-future", 0, questions=[_item("future loop")])
    old = _cp("S-old", 30)  # healing an old session
    out = carry.merge(old, prev, NOW)
    assert out["working_context"]["open_questions"] == []


def test_inputs_not_mutated_and_none_prev_ok():
    new = _cp("S-new")
    snapshot = copy.deepcopy(new)
    assert carry.merge(new, None, NOW) == new
    prev = _cp("S-prev", 1, questions=[_item("q")])
    prev_snapshot = copy.deepcopy(prev)
    carry.merge(new, prev, NOW)
    assert new == snapshot and prev == prev_snapshot


def test_dedup_new_wins_and_inherits_older_first_seen():
    prev = _cp("S-prev", 1, questions=[_item(
        "quorint-ledger reconciliation drops entries when upstream feed pauses",
        days=45)])
    new = _cp("S-new", 0, questions=[_item(
        "quorint-ledger reconciliation still dropping entries on feed pauses",
        days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1  # not duplicated
    assert "still dropping" in qs[0]["text"]      # NEW wording won
    assert qs[0]["first_seen"] == _iso(45)        # age did NOT reset
    assert "carried_from" not in qs[0]            # native item stays native


def test_distinct_items_both_kept():
    prev = _cp("S-prev", 1, questions=[_item("gavotte lint pipeline flaking")])
    new = _cp("S-new", 0, questions=[_item("tervane cache eviction policy unclear")])
    out = carry.merge(new, prev, NOW)
    assert len(out["working_context"]["open_questions"]) == 2


def test_cap_keeps_heaviest_carried_only():
    qs = [_item(f"distinct question number {i} about module alpha-{i} subsystem beta-{i}",
                imp=(i % 10) + 1, days=3) for i in range(12)]
    prev = _cp("S-prev", 1, questions=qs)
    out = carry.merge(_cp("S-new"), prev, NOW, cap=8)
    carried = out["working_context"]["open_questions"]
    assert len(carried) == 8
    # heaviest importance values survived
    kept_imps = sorted((i["importance"] for i in carried), reverse=True)
    assert kept_imps[0] == 10 and min(kept_imps) >= 3


def test_same_item_short_texts_never_fuzzy_match():
    assert carry._same_item("ok", "ok go") is False


def test_in_call_duplicate_prev_items_carry_once():
    # Two prev items with IDENTICAL text: native_texts must pick up the first
    # one as it's appended, so the second (an exact twin) is skipped too.
    prev = _cp("S-prev", 1, questions=[
        _item("quorint reconciliation loop unresolved"),
        _item("quorint reconciliation loop unresolved"),
    ])
    out = carry.merge(_cp("S-new"), prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
