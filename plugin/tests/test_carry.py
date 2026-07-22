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


def test_verbatim_freeze_source_ids_travel_with_quote():
    # #358: source_message_ids ride the same rail as quote_verified — the
    # binding attests THIS quote's origin message. When the freeze overwrites
    # the twin's quote with prev's pinned original, the twin's own ids would
    # bind prev's quote to the wrong turn; prev's ids replace them.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45,
        source_message_ids=["u-orig"])])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_TWIN, days=0, trust="verbatim", quote="different exact words",
        source_message_ids=["u-new"])])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["quote"] == _VERB_QUOTE
    assert qs[0]["source_message_ids"] == ["u-orig"]


def test_verbatim_freeze_pops_twin_ids_when_prev_has_none():
    # Prev pinned quote without a binding (pre-#358 checkpoint): the twin's
    # own ids must not survive attached to a quote they never described.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_TWIN, days=0, trust="verbatim", quote="different exact words",
        source_message_ids=["u-new"])])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert len(qs) == 1
    assert qs[0]["quote"] == _VERB_QUOTE
    assert "source_message_ids" not in qs[0]


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


# --- bind_links (#14): pure text-target -> prev-id binding for supersedes
# links. Never-guess: only a UNIQUE same-kind prev match rewrites. -----------

def test_bind_links_unique_match_rewrites_and_returns_pair():
    prev = _cp("S-prev", 1, decisions=[
        _item("use gateway A for serialize", id="r-old001")])
    merged = _cp("S-new", 0, decisions=[
        _item("use gateway B", id="r-new001",
              links=[{"type": "supersedes",
                      "target": "gateway A serialize choice"}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == [("r-old001", "r-new001", "use gateway A for serialize")]
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == "r-old001"


def test_bind_links_ambiguous_stays_text_no_pair():
    prev = _cp("S-prev", 1, decisions=[
        _item("old zulu rollout plan alpha version one", id="r-old010"),
        _item("old zulu rollout plan beta version two", id="r-old020"),
    ])
    target = "legacy zulu rollout plan alpha beta"
    merged = _cp("S-new", 0, decisions=[
        _item("switch to plan omega", id="r-new002",
              links=[{"type": "supersedes", "target": target}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == target


def test_bind_links_skips_self_and_twin():
    # Native item INHERITED the prev item's id (twin id-inheritance, carry.py
    # ~151-152) and its link target text-matches that SAME prev item.
    orig = "gateway A serialize choice locked"
    prev = _cp("S-prev", 1, decisions=[_item(orig, id="r-old001")])
    merged = _cp("S-new", 0, decisions=[
        _item("gateway A serialize choice still locked", id="r-old001",
              links=[{"type": "supersedes", "target": orig}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == orig


def test_bind_links_ignores_id_shaped_targets_and_malformed():
    prev = _cp("S-prev", 1, decisions=[
        _item("some other decision entirely", id="r-abc123")])
    merged = _cp("S-new", 0, decisions=[
        _item("native decision one", id="r-new003",
              links=[{"type": "supersedes", "target": "r-abc123"}]),
        _item("native decision two", id="r-new004",
              links=["bare", {"type": "supersedes"}]),
    ])
    pairs = carry.bind_links(merged, prev)  # no raise
    assert pairs == []
    decisions = merged["working_context"]["recent_decisions"]
    assert decisions[0]["links"][0]["target"] == "r-abc123"


def test_bind_links_carried_native_does_not_double_count_generic():
    # Reviewer repro: merge() carries prev items verbatim into merged natives
    # (stamped carried_from), so counting them AGAIN in the DF universe pushes
    # shared vocabulary over _GENERIC_DF=3, strips it as generic, and collapses
    # a genuinely AMBIGUOUS target into a false-unique bind. Carried natives
    # must be excluded from the universe — their vocabulary is already
    # represented via prev_items. Ambiguous must stay unbound.
    prev = _cp("S-prev", 1, decisions=[
        _item("alpha bravo charlie delta echo", id="d-old001"),
        _item("alpha bravo charlie foxtrot golf", id="d-old002"),
    ])
    target = "alpha bravo charlie delta echo"
    merged = _cp("S-new", 0, decisions=[
        _item("alpha bravo charlie delta echo", id="d-old001",
              carried_from="S-prev"),  # carried verbatim by merge()
        _item("switch to the new gateway plan", id="d-new001",
              links=[{"type": "supersedes", "target": target}]),
    ])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []  # target matches BOTH prev decisions -> ambiguous
    link = merged["working_context"]["recent_decisions"][1]["links"][0]
    assert link["target"] == target  # unchanged


def test_bind_links_dedupes_triples_but_rewrites_both_links():
    # Two supersedes links on ONE item both resolving to the same prev item:
    # both targets are rewritten, but the returned list carries ONE triple
    # (dedupe by (old_id, new_id), keep first).
    prev = _cp("S-prev", 1, decisions=[
        _item("use gateway A for serialize", id="d-old001")])
    merged = _cp("S-new", 0, decisions=[
        _item("use gateway B", id="d-new001",
              links=[{"type": "supersedes",
                      "target": "gateway A serialize choice"},
                     {"type": "supersedes",
                      "target": "serialize via gateway A"}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == [("d-old001", "d-new001", "use gateway A for serialize")]
    links = merged["working_context"]["recent_decisions"][0]["links"]
    assert links[0]["target"] == "d-old001"
    assert links[1]["target"] == "d-old001"


def test_bind_links_same_kind_only():
    # Target text matches an OPEN QUESTION in prev, not a decision -> the
    # decisions-kind walk never looks at prev open_questions, so no bind.
    text = "gateway A serialize choice locked"
    prev = _cp("S-prev", 1, questions=[_item(text, id="q-old001")])
    merged = _cp("S-new", 0, decisions=[
        _item("switch decision", id="r-new005",
              links=[{"type": "supersedes", "target": text}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == text


# --- reversal guard on the verbatim freeze (#167) ----------------------------
# A native item that REVERSES a prev verbatim decision shares its vocabulary
# (both name the same subject), so _same_item twin-matches them — and the #22
# freeze then overwrites the reversal with the decision it was reversing. The
# reversal signature (the native carries its OWN verified quote AND a
# `supersedes` link aimed at the prev item) must exclude it from twin
# candidacy entirely: no freeze, no id inheritance, prev carried as its own
# item so bind_links can emit the supersede-candidate event.

_REV_PREV = ("Study commitment: target the quorint solutions architect exam "
             "with a ten week plan")
_REV_PREV_QUOTE = "committing to the quorint solutions architect exam"
_REV_NATIVE = ("Drop the quorint solutions architect exam prep entirely; new "
               "target is the plimsol cloud engineer exam")
_REV_NATIVE_QUOTE = "dropping the quorint solutions architect prep entirely"
_REV_TARGET = "quorint solutions architect exam preparation plan"


def _reversal_cps():
    prev = _cp("S-prev", 1, decisions=[_item(
        _REV_PREV, trust="verbatim", quote=_REV_PREV_QUOTE,
        quote_verified=True, id="r-old001", days=10)])
    new = _cp("S-new", 0, decisions=[_item(
        _REV_NATIVE, trust="verbatim", quote=_REV_NATIVE_QUOTE,
        quote_verified=True, days=0,
        links=[{"type": "supersedes", "target": _REV_TARGET}])])
    return prev, new


def test_reversal_native_survives_prev_verbatim_freeze():
    # RED today: the freeze overwrites the reversal's text+quote with the prev
    # decision's, and the reversal is lost from the record.
    prev, new = _reversal_cps()
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    native = next(d for d in ds if not d.get("carried_from"))
    assert native["text"] == _REV_NATIVE          # reversal text intact
    assert native["quote"] == _REV_NATIVE_QUOTE   # its own verified quote intact
    assert native.get("quote_verified") is True
    assert native.get("id") != "r-old001"         # no twin id-inheritance


def test_reversal_prev_item_still_carries_alongside():
    # The reversed decision is NOT silently dropped: it carries as its own item
    # (distinct id) so the render layer can flag it as a supersede-candidate.
    prev, new = _reversal_cps()
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 2
    carried = next(d for d in ds if d.get("carried_from"))
    assert carried["text"] == _REV_PREV
    assert carried["id"] == "r-old001"
    assert carried["carried_from"] == "S-prev"


def test_reversal_then_bind_links_emits_candidate_pair():
    # End of the pipeline the bug suppressed: after merge, the (stamped) native
    # reversal binds its text target to the prev id and bind_links returns the
    # pair — distinct ids, so the self/twin guard no longer eats the event.
    prev, new = _reversal_cps()
    out = carry.merge(new, prev, NOW)
    native = next(d for d in out["working_context"]["recent_decisions"]
                  if not d.get("carried_from"))
    native["id"] = "r-new001"  # what store._stamp_item_ids does pre-bind
    pairs = carry.bind_links(out, prev)
    assert pairs == [("r-old001", "r-new001", _REV_PREV)]


def test_unrelated_supersedes_link_does_not_defeat_freeze():
    # GUARD against over-loosening: a native twin whose supersedes link points
    # somewhere ELSE is a re-statement carrying an unrelated link — the #22
    # freeze must still apply.
    prev = _cp("S-prev", 1, decisions=[_item(
        _REV_PREV, trust="verbatim", quote=_REV_PREV_QUOTE,
        id="r-old001", days=10)])
    new = _cp("S-new", 0, decisions=[_item(
        "the quorint solutions architect exam commitment still stands",
        trust="verbatim", quote="commitment still stands",
        quote_verified=True, days=0,
        links=[{"type": "supersedes",
                "target": "zephyr gateway rollout ordering choice"}])])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["text"] == _REV_PREV             # frozen original won
    assert ds[0]["quote"] == _REV_PREV_QUOTE


def test_reversal_signature_requires_verified_quote():
    # An UNVERIFIED native with a supersedes link is not trusted as a reversal
    # (the quote could be fabricated) — freeze applies as before.
    prev = _cp("S-prev", 1, decisions=[_item(
        _REV_PREV, trust="verbatim", quote=_REV_PREV_QUOTE,
        id="r-old001", days=10)])
    new = _cp("S-new", 0, decisions=[_item(
        _REV_NATIVE, trust="verbatim", quote=_REV_NATIVE_QUOTE,
        quote_verified=False, days=0,
        links=[{"type": "supersedes", "target": _REV_TARGET}])])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["text"] == _REV_PREV             # frozen original won


def test_reversal_id_shaped_target_matches_prev_id():
    # The serializer may emit an already-bound id target; the reversal guard
    # must recognize it against the prev item's id, not just free text. The
    # target must be _ID_SHAPE-valid (hex tail), so this fixture re-ids the
    # prev item — "r-old001" has a non-hex tail and would take the text path.
    prev, new = _reversal_cps()
    prev["working_context"]["recent_decisions"][0]["id"] = "r-abc123"
    new["working_context"]["recent_decisions"][0]["links"][0]["target"] = \
        "r-abc123"
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    native = next(d for d in ds if not d.get("carried_from"))
    assert native["text"] == _REV_NATIVE
    assert len(ds) == 2


def test_reversal_detected_past_malformed_and_empty_link_entries():
    # The link walk must skip junk entries (non-dict, wrong type, missing or
    # blank target) and still find the aimed supersedes link after them.
    prev, new = _reversal_cps()
    native = new["working_context"]["recent_decisions"][0]
    native["links"] = [
        "bare-string",
        {"type": "related", "target": _REV_TARGET},
        {"type": "supersedes"},
        {"type": "supersedes", "target": "   "},
        {"type": "supersedes", "target": 42},
        {"type": "supersedes", "target": _REV_TARGET},
    ]
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 2                            # reversal + carried prev
    native_out = next(d for d in ds if not d.get("carried_from"))
    assert native_out["text"] == _REV_NATIVE       # freeze did not fire


def test_id_shaped_target_for_other_item_does_not_defeat_freeze():
    # An already-bound target aimed at a DIFFERENT item's id is not a reversal
    # of THIS prev item — the freeze must still apply to the twin.
    prev, new = _reversal_cps()
    new["working_context"]["recent_decisions"][0]["links"][0]["target"] = \
        "r-fff999"  # id-shaped, but not the prev item's id
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["text"] == _REV_PREV              # frozen original won


# --- quote_verified must travel with the quote on the freeze path (#167) -----

def test_freeze_pops_stale_quote_verified_when_prev_has_none():
    # Prev verbatim from a pre-#125 checkpoint (no quote_verified key). The
    # freeze swaps in prev's quote; the native's quote_verified=True must NOT
    # survive attached to a quote it never verified.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_TWIN, trust="verbatim", quote="still dropping on pauses",
        quote_verified=True, days=0)])
    out = carry.merge(new, prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert q["quote"] == _VERB_QUOTE
    assert "quote_verified" not in q


# --- loose-target fallback in bind_links (#168) ------------------------------
# Supersession pairs share their subject vocabulary BY NATURE — the subject is
# the identity. When that vocabulary reaches document-frequency 3 across the
# kind (reversal + restatement + prev original), generic subtraction strips it
# and the link target can no longer reach the _same_item floors, so the
# supersession silently never binds. Fallback: when the generic-subtracted pass
# finds ZERO matches, retry without subtraction under the strict >=3 shared
# floor only (no ratio path — terse targets over-fire it), still requiring a
# UNIQUE match (never-guess: ambiguity stays unbound).

def _loose_target_cps():
    prev = _cp("S-prev", 1, decisions=[
        _item("commit to the aws solutions architect exam ten week plan",
              id="d-old001"),
        _item("zephyr gateway rollout ordering unrelated", id="d-old002"),
    ])
    merged = _cp("S-new", 0, decisions=[
        _item("drop the aws solutions architect prep entirely",
              id="d-new001",
              links=[{"type": "supersedes",
                      "target": "aws solutions architect advanced preparation"}]),
        _item("aws solutions architect course refund requested",
              id="d-new002"),
    ])
    return prev, merged


def test_bind_links_generic_stripped_target_falls_back_to_unique_bind():
    # RED today: {aws, solutions, architect} hit DF>=3 across the kind ->
    # generic -> the target's residue {advanced, preparation} shares nothing
    # with the prev item -> pass 1 finds zero -> link stays free text. The
    # fallback must bind it: full-vocabulary overlap is 3 shared terms and the
    # match is unique.
    prev, merged = _loose_target_cps()
    pairs = carry.bind_links(merged, prev)
    assert pairs == [("d-old001", "d-new001",
                      "commit to the aws solutions architect exam ten week "
                      "plan")]
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == "d-old001"


def test_bind_links_fallback_refuses_ambiguous_full_vocab():
    # Two prev items both reach >=3 shared full-vocabulary terms with the
    # target -> fallback must stay unbound (a wrong bind fabricates
    # provenance).
    prev, merged = _loose_target_cps()
    prev["working_context"]["recent_decisions"][1] = _item(
        "retake the aws solutions architect exam next quarter", id="d-old002")
    pairs = carry.bind_links(merged, prev)
    assert pairs == []
    link = merged["working_context"]["recent_decisions"][0]["links"][0]
    assert link["target"] == "aws solutions architect advanced preparation"


def test_bind_links_fallback_requires_three_full_shared_terms():
    # Documented limit (the terse-target class, live specimen): "Tutorials
    # Dojo purchase plan" shares only {tutorials, dojo} with the prev item —
    # 2 < 3 shared and ratio 2/4 = 0.5 < 0.6, so pass 1 misses; the fallback
    # (>=3 full-vocabulary shared, no ratio path) must ALSO refuse. Two terms
    # is not identity — the prompt-side fix (name the old decision
    # specifically) is the answer for this class, not looser matching.
    prev = _cp("S-prev", 1, decisions=[
        _item("use tutorials dojo practice exam sets for week nine",
              id="d-old001")])
    merged = _cp("S-new", 0, decisions=[
        _item("switch to the plimsol skills program instead",
              id="d-new001",
              links=[{"type": "supersedes",
                      "target": "tutorials dojo purchase plan"}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []


def test_bind_links_pass1_ambiguity_never_falls_back():
    # Pass 1 finding MULTIPLE matches is a verdict (ambiguous), not a miss —
    # the fallback only runs on zero matches. Mirrors
    # test_bind_links_ambiguous_stays_text_no_pair with the fallback present.
    prev = _cp("S-prev", 1, decisions=[
        _item("old zulu rollout plan alpha version one", id="r-old010"),
        _item("old zulu rollout plan beta version two", id="r-old020"),
    ])
    merged = _cp("S-new", 0, decisions=[
        _item("switch to plan omega", id="r-new002",
              links=[{"type": "supersedes",
                      "target": "legacy zulu rollout plan alpha beta"}])])
    pairs = carry.bind_links(merged, prev)
    assert pairs == []


def test_freeze_copies_prev_quote_verified_with_quote():
    # Prev carries its own verdict — it rides along with the pinned quote.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE,
        quote_verified=True, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert q["quote"] == _VERB_QUOTE
    assert q["quote_verified"] is True


# --- quantity-conflict guard on unlinked twin false merge (#173) ------------
# A native item that UPDATES a prior verbatim decision (not a re-statement,
# not an explicit #167 reversal) can share enough subject vocabulary to
# twin-match under _same_item's term-overlap rule alone. When the two texts
# state DIFFERENT numbers, that is structural evidence they are distinct
# items, not a reworded twin — the guard must block the match regardless of
# term overlap, so the #22 freeze never fires and the update survives.

_QTY_PREV = ("Study commitment: target exam X with a ten-week plan at 6 "
             "hours per week")
_QTY_PREV_QUOTE = "ten-week plan at 6 hours per week"
_QTY_NATIVE = ("Maintain 6 hours per week study commitment, compressed "
               "into 3 weeks")


def test_quantity_conflict_blocks_twin_merge_native_update_survives():
    # #173 live specimen: {ten, 6} vs {6, 3} — neither is a subset of the
    # other, a genuine two-sided numeric mismatch. RED today: term overlap
    # ({study, commitment, week, hours}) alone twin-matches them and the #22
    # freeze overwrites the native update's text+quote with the prev
    # decision's, silently losing the "compressed into 3 weeks" change.
    prev = _cp("S-prev", 1, decisions=[_item(
        _QTY_PREV, trust="verbatim", quote=_QTY_PREV_QUOTE, days=10)])
    new = _cp("S-new", 0, decisions=[_item(_QTY_NATIVE, days=0)])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    native = next(d for d in ds if not d.get("carried_from"))
    assert native["text"] == _QTY_NATIVE          # update text intact
    assert native.get("trust") != "verbatim"       # never frozen
    assert "carried_from" not in native
    carried = next(d for d in ds if d.get("carried_from"))
    assert carried["text"] == _QTY_PREV            # prev survives separately
    assert carried["quote"] == _QTY_PREV_QUOTE
    assert carried["trust"] == "verbatim"
    assert len(ds) == 2


def test_quantity_equivalence_number_word_and_digit_still_freezes():
    # GUARD against over-firing: "ten"/"six" and "10"/"6" normalize to the
    # SAME value set -> no conflict -> the #22 freeze still applies exactly
    # as before the guard existed.
    prev = _cp("S-prev", 1, decisions=[_item(
        "Study commitment: target exam Y with a ten week plan at six hours "
        "per week", trust="verbatim",
        quote="ten week plan at six hours per week", days=45)])
    new = _cp("S-new", 0, decisions=[_item(
        "Study commitment: maintaining a 10 week plan at 6 hours per week",
        days=0)])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["trust"] == "verbatim"
    assert "ten week plan" in ds[0]["text"]         # frozen original won


def test_quantity_subset_drop_still_freezes_restatement():
    # DESIGN CHOICE: a restatement that DROPS one number but keeps the other
    # ({10, 6} -> {6}) is a subset, not a conflict — #22's "restatement drops
    # detail" behavior must still freeze it. Only a two-sided mismatch (#173
    # specimen above) counts as conflict.
    prev = _cp("S-prev", 1, decisions=[_item(
        _QTY_PREV, trust="verbatim", quote=_QTY_PREV_QUOTE, days=45)])
    new = _cp("S-new", 0, decisions=[_item(
        "Study commitment still on at 6 hours per week", days=0)])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["trust"] == "verbatim"
    assert ds[0]["text"] == _QTY_PREV               # frozen original won


def test_quantity_conflict_dedup_path_prev_items_carry_separately():
    # Same guard on the plain dedup path (#31 item 9's carry-once): two PREV
    # items reworded-twins of each other on term overlap alone, but stating
    # CONFLICTING quantities, must both carry rather than collapsing into one.
    prev = _cp("S-prev", 1, decisions=[
        _item("study commitment ten week plan six hours weekly", days=5),
        _item("study commitment three week plan six hours weekly", days=3),
    ])
    out = carry.merge(_cp("S-new"), prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 2


# --- quantity extraction hardening (#173 review round 2) --------------------
# Adversarial review found the naive extractor false-conflicts on ordinary
# reformatting of the SAME number: "1,000" tokenized digit-by-digit around
# the comma, "twenty-five" read as two separate words instead of one
# compound, and "2.5" split into {2, 5} at the decimal point. All three bugs
# point the same direction — over-blocking a legitimate freeze/merge — so
# each gets a direct unit test on `_quantity_tokens` plus a merge-level
# regression proving the freeze still fires end to end.

def test_quantity_tokens_strips_thousand_separators():
    assert carry._quantity_tokens("1,000 users churned") == frozenset({1000})
    assert carry._quantity_tokens("1000 users churned") == frozenset({1000})


def test_quantity_tokens_combines_compound_number_words():
    assert carry._quantity_tokens("twenty-five students enrolled") == \
        frozenset({25})
    assert carry._quantity_tokens("twenty five students enrolled") == \
        frozenset({25})
    assert carry._quantity_tokens("25 students enrolled") == frozenset({25})


def test_quantity_tokens_decimal_is_one_value():
    assert carry._quantity_tokens("throughput at 2.5 times baseline") == \
        frozenset({2.5})


def test_quantity_tokens_zero():
    assert carry._quantity_tokens("zero regressions found") == frozenset({0})


def test_quantity_comma_thousands_restatement_still_freezes():
    # "1,000" vs "1000" must normalize to the same value -> no conflict ->
    # the #22 freeze fires exactly as it would with no comma at all.
    prev = _cp("S-prev", 1, decisions=[_item(
        "Budget approved: 1,000 seats for the rollout", trust="verbatim",
        quote="1,000 seats for the rollout", days=45)])
    new = _cp("S-new", 0, decisions=[_item(
        "Budget approved: 1000 seats confirmed for the rollout", days=0)])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["trust"] == "verbatim"
    assert "1,000 seats" in ds[0]["text"]           # frozen original won


def test_quantity_compound_word_restatement_still_freezes():
    # "twenty-five" vs "25" must normalize to the same value -> no conflict.
    prev = _cp("S-prev", 1, decisions=[_item(
        "Team target: twenty-five signups this month", trust="verbatim",
        quote="twenty-five signups this month", days=45)])
    new = _cp("S-new", 0, decisions=[_item(
        "Team target: 25 signups confirmed this month", days=0)])
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    assert len(ds) == 1
    assert ds[0]["trust"] == "verbatim"
    assert "twenty-five signups" in ds[0]["text"]   # frozen original won


# --- quantity guard interaction with the #167 reversal pipeline (#173 round
# 2 pinning test) -------------------------------------------------------------
# A reversal's native text can legitimately state a DIFFERENT quantity than
# the prev item it reverses (that's the whole point of a reversal). The
# guard already produces the correct outcome here on its own (no twin, so no
# freeze — the same result #167's carve-out targets), but nothing pinned
# that the REST of the pipeline (prev still carries alongside, bind_links
# still resolves the supersedes link) keeps working when the guard is the
# thing blocking the twin match instead of `_is_reversal_of`.

_REV_QTY_PREV = ("Study commitment: target the quorint solutions architect "
                  "exam with a 10-week plan at 6 hours per week")
_REV_QTY_PREV_QUOTE = "10-week plan at 6 hours per week"
_REV_QTY_NATIVE = ("Drop the 10-week quorint architect plan entirely; "
                    "committing to a 3-week sprint instead")
_REV_QTY_NATIVE_QUOTE = "committing to a 3-week sprint instead"
_REV_QTY_TARGET = "quorint solutions architect exam 10-week plan"


def _qty_reversal_cps():
    prev = _cp("S-prev", 1, decisions=[_item(
        _REV_QTY_PREV, trust="verbatim", quote=_REV_QTY_PREV_QUOTE,
        quote_verified=True, id="r-qty001", days=10)])
    new = _cp("S-new", 0, decisions=[_item(
        _REV_QTY_NATIVE, trust="verbatim", quote=_REV_QTY_NATIVE_QUOTE,
        quote_verified=True, days=0,
        links=[{"type": "supersedes", "target": _REV_QTY_TARGET}])])
    return prev, new


def test_quantity_conflict_guard_does_not_defeat_reversal_pipeline():
    # prev {10, 6} vs native {10, 3} conflict on quantity alone (guard blocks
    # the twin before _is_reversal_of even runs) -> same outcome #167 already
    # guarantees: reversal text+quote survive untouched, prev carries
    # alongside, and bind_links still resolves the supersedes link (whose
    # target text cites only the OLD, non-conflicting "10-week" wording)
    # into a supersede-candidate pair.
    prev, new = _qty_reversal_cps()
    out = carry.merge(new, prev, NOW)
    ds = out["working_context"]["recent_decisions"]
    native = next(d for d in ds if not d.get("carried_from"))
    assert native["text"] == _REV_QTY_NATIVE
    assert native["quote"] == _REV_QTY_NATIVE_QUOTE
    assert len(ds) == 2
    carried = next(d for d in ds if d.get("carried_from"))
    assert carried["text"] == _REV_QTY_PREV
    assert carried["id"] == "r-qty001"
    native["id"] = "r-qty-new"  # what store._stamp_item_ids does pre-bind
    pairs = carry.bind_links(out, prev)
    assert pairs == [("r-qty001", "r-qty-new", _REV_QTY_PREV)]


# --- quote_verified:false is a fresh-only signal (#209) ----------------------
# A False stamp means THIS serialize's verify_quotes failed the item. The
# origin checkpoint keeps it (forensics); a carried copy never re-ran the
# check, so inheriting False makes checkpoint-level metrics double-count one
# failure forever. Carry strips False on both paths; True (a real attestation
# bound to the pinned quote) still travels.

def test_plain_carry_strips_inherited_quote_verified_false():
    # Downgraded-at-origin item (trust already inferred, quote retained for
    # forensics, False stamp). Plain no-twin carry must drop ONLY the stamp.
    prev = _cp("S-prev", 1, questions=[_item(
        "quorint gateway retry loop still unresolved after downgrade",
        quote="retry loop still unresolved", quote_verified=False, days=5)])
    out = carry.merge(_cp("S-new"), prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert "quote_verified" not in q
    assert q["quote"] == "retry loop still unresolved"  # forensics untouched
    assert q["trust"] == "inferred"


def test_plain_carry_keeps_true_attestation():
    # Regression guard: True is a real attestation and must keep traveling.
    prev = _cp("S-prev", 1, questions=[_item(
        "quorint gateway retry loop still unresolved verbatim",
        trust="verbatim", quote="retry loop still unresolved",
        quote_verified=True, days=5)])
    out = carry.merge(_cp("S-new"), prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert q["quote_verified"] is True
    assert q["trust"] == "verbatim"


def test_freeze_pops_illegal_prev_quote_verified_false():
    # Post-#125 a verbatim item cannot legally carry False (the downgrade
    # strips trust), but pre-#125 and hand-edited checkpoints exist. The twin
    # freeze must treat anything but True as absent, not copy it.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE,
        quote_verified=False, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert q["quote"] == _VERB_QUOTE
    assert "quote_verified" not in q


def test_chained_carry_false_stamp_stays_absent():
    # The stamp is stripped on the FIRST carry; a second merge of the already
    # -clean copy must not resurrect it (absence is the stable state).
    cp_a = _cp("S-a", 2, questions=[_item(
        "quorint gateway retry loop still unresolved after downgrade",
        quote="retry loop still unresolved", quote_verified=False, days=5)])
    hop1 = carry.merge(_cp("S-b", 1), cp_a, NOW)
    assert "quote_verified" not in hop1["working_context"]["open_questions"][0]
    hop2 = carry.merge(_cp("S-c", 0), hop1, NOW)
    q = hop2["working_context"]["open_questions"][0]
    assert "quote_verified" not in q
    assert q["quote"] == "retry loop still unresolved"
    assert q["carried_from"] == "S-a"


# ---- #215: last_verified survives carry ----
#
# Checkpoints are append-only — last_verified is stamped ONLY at serialize
# time (#125's verify_quotes). Carry must never rewrite or refresh it on its
# own; the plain path is a straight deepcopy (nothing to prove but that no
# strip was added), and the twin-freeze path applies a NEWER-wins rule since
# a re-discussed-and-re-verified quote was just world-checked again.

def test_plain_carry_preserves_last_verified_unchanged():
    prev = _cp("S-prev", 1, questions=[_item(
        "quorint gateway retry loop still unresolved", days=5,
        last_verified=_iso(4))])
    out = carry.merge(_cp("S-new"), prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert q["last_verified"] == _iso(4)  # byte-identical, never refreshed


def test_plain_carry_with_no_last_verified_stays_absent():
    prev = _cp("S-prev", 1, questions=[_item(
        "quorint gateway retry loop still unresolved", days=5)])
    out = carry.merge(_cp("S-new"), prev, NOW)
    q = out["working_context"]["open_questions"][0]
    assert "last_verified" not in q


def test_twin_freeze_fresh_last_verified_wins_over_prev():
    # Native twin was JUST re-verified this session (fresh, newer) — prev's
    # older stamp must NOT overwrite it.
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45,
        last_verified=_iso(44))])
    new = _cp("S-new", 0, questions=[_item(
        _VERB_TWIN, days=0, last_verified=_iso(0))])
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert qs[0]["last_verified"] == _iso(0)  # fresh kept, newer wins


def test_twin_freeze_propagates_prev_last_verified_when_twin_has_none():
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45,
        last_verified=_iso(44))])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])  # no stamp
    out = carry.merge(new, prev, NOW)
    qs = out["working_context"]["open_questions"]
    assert qs[0]["last_verified"] == _iso(44)  # propagated from prev, only source


def test_twin_freeze_neither_has_last_verified_stays_absent():
    prev = _cp("S-prev", 1, questions=[_item(
        _VERB_ORIG, trust="verbatim", quote=_VERB_QUOTE, days=45)])
    new = _cp("S-new", 0, questions=[_item(_VERB_TWIN, days=0)])
    out = carry.merge(new, prev, NOW)
    assert "last_verified" not in out["working_context"]["open_questions"][0]


def test_carry_preserves_scene(monkeypatch):
    # #317: scene rides the item deepcopy — pin it so a future selective-copy
    # refactor cannot silently drop episodic context
    prev = _cp("S-old", questions=[{"text": "does the retry nonce land?",
                                    "trust": "inferred",
                                    "scene": "asked right after the gateway pinned a bad response"}])
    new = _cp("S-new")
    merged = carry.merge(new, prev, NOW)
    carried = merged["working_context"]["open_questions"]
    assert any(i.get("scene") == "asked right after the gateway pinned a bad response"
               for i in carried)
