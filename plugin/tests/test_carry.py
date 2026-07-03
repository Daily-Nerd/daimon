"""Deterministic carry (#33 Phase 2): merge() folds the previous checkpoint's
unresolved items into the new one by code. Pure function — all clock/config
injected."""
import copy

from daimon_briefing import carry

NOW = 1_760_000_000.0  # arbitrary fixed epoch

# Live false-merge specimen (2026-07-02): two UNRELATED items that matched on
# exactly the generic terms {data, field, validation}. See #13.
_SPEC_A = ("First external user validation — the core adoption-arc objective "
           "that unblocks _MIN_OVERLAP field data, DAIMON_TEAM validation, and "
           "teammate-noise research questions")
_SPEC_B = ("Q-STALE + multi-cycle degradation validation — parked on LLM "
           "budget. Need field data: what do 20 serialize cycles do to a "
           "long-lived open loop, and how does that inform decay tuning?")
# Sibling native item carrying the same generic vocabulary, so {data, field,
# validation} each reach document-frequency 3 across the kind (A, sibling, B).
_SPEC_SIB = "extra validation of the data field mapping"


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


def test_generic_overlap_does_not_false_merge_specimen():
    # Live #13 specimen: fresh native A and unrelated carried B share only the
    # generic terms {data, field, validation}. B must survive as its OWN item;
    # A must NOT inherit B's older birth stamp.
    new = _cp("S-new", 0, questions=[
        _item(_SPEC_A, days=0), _item(_SPEC_SIB, days=1)])
    prev = _cp("S-prev", 1, questions=[_item(_SPEC_B, imp=7, days=45)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    texts = [q["text"] for q in qs]
    assert _SPEC_B in texts                       # B kept, not erased
    b_item = next(q for q in qs if q["text"] == _SPEC_B)
    assert b_item["carried_from"] == "S-prev"
    a_item = next(q for q in qs if q["text"] == _SPEC_A)
    assert a_item["first_seen"] == _iso(0)        # A did NOT inherit B's stamp
    assert "carried_from" not in a_item
    assert len(qs) == 3


def test_same_item_generic_filter_is_the_fix_not_a_threshold():
    generic = frozenset({"data", "field", "validation"})
    assert carry._same_item(_SPEC_A, _SPEC_B, generic) is False
    assert carry._same_item(_SPEC_A, _SPEC_B) is True   # unfiltered: the bug


def test_specific_twin_still_merges_and_inherits_age():
    # Two rewordings sharing SPECIFIC low-DF terms must still match (run-02
    # behavior): the guard filters vocabulary, not identity.
    old = _item("quorint-ledger reconciliation drops entries when upstream "
                "feed pauses", days=45)
    new_twin = _item("quorint-ledger reconciliation still dropping entries on "
                     "feed pauses", days=0)
    prev = _cp("S-prev", 1, questions=[
        old, _item("unrelated gavotte pipeline flaking noise", days=3)])
    new = _cp("S-new", 0, questions=[
        new_twin, _item("tervane cache eviction unclear noise", days=1)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    twin = next(q for q in qs if "still dropping" in q["text"])
    assert twin["first_seen"] == _iso(45)               # matched -> age inherited
    assert not any("drops entries" in q["text"] for q in qs)  # not duplicated


def test_post_filter_floor_blocks_single_shared_term():
    generic = frozenset({"data", "field", "validation"})
    a = "data field validation alpha"          # filtered -> {alpha}
    b = "data field validation alpha bravo"    # filtered -> {alpha, bravo}
    # ratio would be 1/1 = 1.0 >= _MIN_RATIO without the floor; floor blocks it
    assert carry._same_item(a, b, generic) is False


def test_generic_terms_df_boundary():
    texts = ["zeta omega alpha", "zeta omega beta", "omega gamma delta"]
    generic = carry._generic_terms(texts)   # k defaults to _GENERIC_DF (3)
    assert "omega" in generic       # 3 distinct texts -> generic
    assert "zeta" not in generic    # exactly 2 distinct texts -> not generic


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
