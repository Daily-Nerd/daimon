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


# --- verbatim-freeze on re-discovery (#22): reconsolidation defense ----------
# A [✓ verbatim] item carries an immutable pinned quote (D-006). When a later
# session re-discusses it and the serializer emits a reworded native twin, the
# prev's frozen original text+quote+trust must win — recall must NOT re-write a
# verbatim item (Nader/Schafe/LeDoux reconsolidation; daimon has a verbatim
# store the brain lacks). Inferred beliefs still evolve, unchanged.

_VERB_ORIG = ("quorint-ledger reconciliation drops entries when upstream "
              "feed pauses")
_VERB_QUOTE = "drops entries when upstream feed pauses"
_VERB_TWIN = ("quorint-ledger reconciliation still dropping entries on "
              "feed pauses")


def test_verbatim_prev_freezes_reworded_native_twin():
    # CORE: prev verbatim + reworded (here inferred) native twin -> the merged
    # item is the frozen original text+quote, trust stays verbatim. RED today:
    # "new wording wins" keeps the reworded native text and drops the quote.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["text"] == _VERB_ORIG          # frozen original, not reworded
    assert qs[0]["quote"] == _VERB_QUOTE        # pinned quote survived recall
    assert qs[0]["trust"] == "verbatim"         # not downgraded to inferred
    assert qs[0]["first_seen"] == _iso(45)      # older birth stamp preserved


def test_inferred_prev_still_reconsolidates_new_wording_wins():
    # GUARD against over-freezing: an inferred prev item is ALLOWED to evolve.
    # New wording wins exactly as before the #22 fix; nothing is frozen.
    prev = _cp("S-prev", 1, questions=[_item(_VERB_ORIG, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert "still dropping" in qs[0]["text"]    # NEW wording won
    assert qs[0]["trust"] == "inferred"         # NOT frozen to verbatim
    assert qs[0]["first_seen"] == _iso(45)      # age still inherited


def test_verbatim_identical_twin_survives_byte_identical():
    # No-op path: native item already byte-identical to the prev verbatim. Exact
    # text is caught by the idempotency guard before freeze; assert nothing is
    # corrupted (text+quote+trust intact, still one item).
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["text"] == _VERB_ORIG
    assert qs[0]["quote"] == _VERB_QUOTE
    assert qs[0]["trust"] == "verbatim"


def test_verbatim_freeze_inherits_older_first_seen():
    # first_seen birth-stamp inheritance still works on the freeze path: the
    # native twin has a NEWER stamp; the older prev original stamp must win,
    # AND the payload is frozen to verbatim.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert qs[0]["first_seen"] == _iso(45)      # older birth stamp inherited
    assert qs[0]["trust"] == "verbatim"         # payload frozen alongside


def test_two_verbatim_twins_different_quotes_older_original_wins():
    # DESIGN CHOICE: a re-discovered item that is ALSO verbatim but carries a
    # DIFFERENT quote -> freeze to the OLDER pinned original (prev). The thesis
    # is don't-erode; _same_item's asymmetric bias (a false merge is worse than
    # a false non-merge) favors keeping the original over the reworded requote.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_TWIN, trust="verbatim",
        quote="still dropping entries on feed pauses", days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["text"] == _VERB_ORIG                    # older original text
    assert qs[0]["quote"] == _VERB_QUOTE                  # older pinned quote
    assert qs[0]["trust"] == "verbatim"


def test_in_call_reworded_prev_twins_carry_once():
    # #31 item 9: two prev items that are REWORDED twins of each other (fuzzy
    # _same_item hit, no exact-text match, no native twin) must not both
    # carry — the carry-once intent covers fuzzy twins, not just byte-exact.
    prev = _cp("S-prev", 1, questions=[
        _item("quorint ledger reconciliation loop drops entries"),
        _item("quorint ledger reconciliation loop dropping entries nightly"),
    ])
    out = carry.merge(_cp("S-new"), prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1


# --- id inheritance + resolved-skip (#102): merge stays pure, caller injects
# the resolved set as a frozenset of item_ref strings. -----------------------

def test_twin_inherits_prev_id():
    prev = _cp("S-prev", 1, questions=[
        _item("cache guard holds", id="o-aaa111")])
    new = _cp("S-new", 0, questions=[
        _item("the cache guard is holding", days=0)])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["id"] == "o-aaa111"


def test_resolved_prev_item_does_not_carry():
    prev = _cp("S-prev", 1, questions=[
        _item("dead loop no longer relevant", id="o-dead01"),
        _item("control sibling loop still open", id="o-alive1")])
    out = carry.merge(_cp("S-new"), prev, NOW, resolved=frozenset({"o-dead01"}))
    qs = out["working_context"]["open_questions"]
    texts = [q["text"] for q in qs]
    assert "dead loop no longer relevant" not in texts
    assert "control sibling loop still open" in texts


def test_resolved_prev_with_native_twin_still_inherits_id_and_native_survives():
    prev = _cp("S-prev", 1, questions=[
        _item("dead loop no longer relevant", id="o-dead01")])
    new = _cp("S-new", 0, questions=[
        _item("dead loop is no longer relevant now", days=0)])
    out = carry.merge(new, prev, NOW, resolved=frozenset({"o-dead01"}))
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1  # native twin never dropped
    assert qs[0]["id"] == "o-dead01"


def test_merge_without_resolved_kwarg_unchanged():
    prev = _cp("S-prev", 1, questions=[_item("zephyr ledger drop unresolved")])
    new = _cp("S-new")
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["text"] == "zephyr ledger drop unresolved"
    assert qs[0]["carried_from"] == "S-prev"
