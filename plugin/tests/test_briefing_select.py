"""#1128: briefing.select and render_selection, pinned as invariants over a
budget sweep on a real-shaped checkpoint (the one that overflowed the 11 KB
ceiling on 2026-10-03)."""

import datetime as dt
import re

import pytest

from daimon_briefing import briefing

NOW = 1_800_000_000.0


def _iso(days_before_now):
    t = dt.datetime.fromtimestamp(NOW - days_before_now * 86400, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(tag, n):
    base = f"{tag} " + "padding words about the state of things " * 8
    return base[:n].rstrip()


EXTERNAL_AGES = [18, 17, 15, 13, 11, 11, 10, 10, 8, 8, 3, 3, 1]
LOOP_AGES = [16, 14, 12, 9, 8, 6, 5, 4, 3, 2, 1]


def _item(tag, i, n, **extra):
    return {"id": f"{tag[0]}-{i:06x}", "text": _text(f"{tag}-{i:02d}", n),
            "trust": "inferred", **extra}


def _fixture_checkpoint():
    external = []
    for i, age in enumerate(EXTERNAL_AGES):
        it = _item("ext", i, 100 + (i * 11) % 150, importance=8 + i % 3
                   if age > 7 else 5 + i % 3,
                   carried_from="S-prev", external_state=True,
                   last_verified=_iso(age), first_seen=_iso(age + 1))
        if i % 3 == 0:
            it["trust"] = "verbatim"
            it["quote"] = ("the exact words said that day " * 12)[:60 + i * 25]
        external.append(it)
    loops = [_item("loop", i, 110 + (i * 13) % 140, importance=5 + i % 5,
                   carried_from="S-prev", last_verified=_iso(a),
                   first_seen=_iso(a + 1))
             for i, a in enumerate(LOOP_AGES)]
    native = [_item("ndec", i, 100 + (i * 17) % 150, importance=5 + i % 4,
                    first_seen=_iso(0)) for i in range(19)]
    carried_dec = [_item("cdec", i, 100 + (i * 7) % 150, importance=4 + i % 6,
                         carried_from="S-prev", first_seen=_iso(3 + i))
                   for i in range(24)]
    beliefs = [_item("bel", i, 100 + (i * 19) % 140, importance=3 + i % 4,
                     first_seen=_iso(0)) for i in range(10)]
    doubts = [_item("doubt", i, 100 + (i * 9) % 150, importance=2 + i % 5,
                    carried_from="S-prev", first_seen=_iso(5 + i))
              for i in range(24)]
    return {
        "session_id": "S-real",
        "working_context": {
            "active_topic": {"text": "the active topic", "trust": "inferred"},
            "open_questions": external + loops,
            "recent_decisions": native + carried_dec,
        },
        "epistemic_snapshot": {"strong_beliefs": beliefs,
                               "uncertainties": doubts,
                               "contradictions_flagged": []},
    }


def _annotated_b(cp=None, mutate=None):
    cp = cp or _fixture_checkpoint()
    if mutate:
        mutate(cp)
    out = briefing.annotate(cp, briefing.AnnotateContext(route="/repo/x"), NOW)
    return briefing.build(out.checkpoint, now=NOW)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "0")  # build uncapped
    monkeypatch.setenv("DAIMON_STALE_DAYS", "7")


def _sel(b, budget, **kw):
    return briefing.select(b, budget, NOW, **kw)


def _bytes(sel):
    return len(briefing.render_selection(sel).encode("utf-8"))


def _texts(items):
    return [i["text"] for i in items]


RULINGS = [briefing._RULING_HEADER,
           "- R-1: never let a briefing spill past the host's preview limit."]


def _min_budget(b, **kw):
    sel = _sel(b, 1, **kw)
    return _bytes(sel) - (sel.overage or 0) if sel.overage else _bytes(sel)


def _budgets(b, **kw):
    full = _bytes(_sel(b, None, **kw))
    base = _bytes(_sel(b, 1, **kw))
    return sorted({*range(base, full + 200, 173), full}, reverse=True)


def test_unbounded_budget_keeps_everything_but_the_cap(monkeypatch):
    b = _annotated_b()
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    sel = _sel(b, None)
    assert len(sel.kept["decisions"]) == 10
    assert sel.dropped_for("decisions", "cap") == sel.dropped["decisions"]
    assert len(sel.dropped["decisions"]) == 33
    assert sel.dropped_for("decisions", "budget") == []
    # cap spends every slot on native decisions: the last 10 of 19
    assert all(not briefing._is_carried(d) for d in sel.kept["decisions"])
    for s in ("external", "open_loops", "beliefs", "uncertainties"):
        assert len(sel.kept[s]) == sel.totals[s]


def test_budget_sweep_invariants(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    originals = {i["text"]: i for s in briefing._ITEM_SECTIONS
                 for i in b[s]}
    floor = [d for d in b["decisions"] if not briefing._is_carried(d)][-3:]
    saw_pressure = False
    previous_dropped = None
    prev_lowered = False
    for budget in _budgets(b, rulings=RULINGS):
        sel = _sel(b, budget, rulings=RULINGS)
        text = briefing.render_selection(sel)
        # 0. deterministic: a pure function of its inputs
        assert text == briefing.render_selection(
            _sel(b, budget, rulings=RULINGS))
        # 1. greeting first; protected items present
        assert text.splitlines()[0].startswith("While you were away")
        for line in RULINGS:
            assert line in text
        assert "Active topic: the active topic" in text
        if not sel.overage:
            assert len(text.encode("utf-8")) <= budget, budget
            # the newest native decision is always shown (the floor only
            # shrinks, never below one)
            kept_native = [i["id"] for i in sel.kept["decisions"]
                           if not briefing._is_carried(i)]
            all_native = [d["id"] for d in b["decisions"]
                          if not briefing._is_carried(d)]
            assert all_native[-1] in kept_native, budget
            # and the floor is whole unless the fixed order had to cut it
            if len(kept_native) < 3:
                assert sel.dropped_for("decisions", "budget")
            floor_cut = [d for d in floor if d["id"] not in kept_native]
            if floor_cut:
                # a lowered floor means every older candidate decision went
                assert not [i for i in sel.kept["decisions"]
                            if i["id"] not in {d["id"] for d in floor}]
        # 2. prefix property: what budget dropped is a prefix of the order
        floor_ids = {d["id"] for d in floor}
        budget_dropped = {i["id"] for s in briefing._ITEM_SECTIONS
                          for i in sel.dropped_for(s, "budget")}
        candidates_dropped = budget_dropped - floor_ids
        in_order = [it["id"] for _, it in sel.order]
        assert set(in_order[:len(candidates_dropped)]) == candidates_dropped, \
            budget
        # 3. dropped sets only grow as the budget shrinks
        lowered = len([i for i in sel.kept["decisions"] if not briefing._is_carried(i)]) < 3
        if previous_dropped is not None and not lowered and not prev_lowered:
            assert previous_dropped <= budget_dropped, budget
        previous_dropped = budget_dropped
        prev_lowered = lowered
        saw_pressure = saw_pressure or bool(budget_dropped)
        # 4. stale first: while any non-flagged candidate outside the stale
        #    tier is gone, no non-flagged stale carried one remains
        kept_all = [(s, i) for s in briefing._ITEM_SECTIONS
                    for i in sel.kept[s]]
        stale_kept = [i for s, i in kept_all
                      if briefing._stale_days_of(i) is not None
                      and not briefing._is_flagged(i)]
        fresh_dropped = [i for s in briefing._ITEM_SECTIONS
                         for i in sel.dropped_for(s, "budget")
                         if briefing._stale_days_of(i) is None
                         and not briefing._is_flagged(i)
                         and i["id"] not in floor_ids]
        if fresh_dropped:
            assert stale_kept == [], budget
        # 5. verbatim text byte-identical; items never cut mid-line
        for s, i in kept_all:
            if i.get("trust") == "verbatim":
                assert i["text"] == originals[i["text"]]["text"]
                assert i["quote"].strip() in text
            assert briefing._line(i, False, s in briefing.BRIEFABLE_SECTIONS) \
                in text
        # 6. note counts equal the manifest
        for s in briefing._ITEM_SECTIONS:
            note = briefing.section_note(sel, s)
            lost = len(sel.dropped[s])
            if not lost:
                assert note is None
                continue
            m = re.search(r"\((\d+) of (\d+) shown", note)
            assert int(m.group(1)) == len(sel.kept[s])
            assert int(m.group(2)) == len(sel.kept[s]) + lost
            assert note in text
    assert saw_pressure


def test_stale_carried_goes_before_fresh_native_decisions(monkeypatch):
    # The acceptance criterion of #1128: a tight budget drops the stale
    # carried VERIFY items and not the newest native decisions.
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    full = _bytes(_sel(b, None))
    sel = _sel(b, full - 2500)
    assert len(sel.kept["decisions"]) == 10
    assert all(not briefing._is_carried(d) for d in sel.kept["decisions"])
    gone = [i for s in briefing._ITEM_SECTIONS
            for i in sel.dropped_for(s, "budget")]
    assert gone
    # everything the budget took is a stale carried item, nothing fresh
    assert all(briefing._stale_days_of(i) is not None for i in gone)
    # and no fresh VERIFY item went while a stale one is still shown
    fresh_gone = [i for i in sel.dropped_for("external", "budget")
                  if briefing._stale_days_of(i) is None]
    assert fresh_gone == []


def test_decision_floor_holds_at_the_smallest_budget(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    sel = _sel(b, 1, rulings=RULINGS)
    native = [d for d in b["decisions"] if not briefing._is_carried(d)]
    assert sel.overage
    # panels absent here, so the floor shrinks to one only when it must:
    assert _texts(sel.kept["decisions"]) == _texts(native[-1:])
    roomy = _sel(b, _bytes(_sel(b, None)) // 3)
    assert _texts(roomy.kept["decisions"])[-3:] == _texts(native[-3:])


@pytest.mark.parametrize("cap,expected", [(1, 1), (2, 2), (3, 3), (10, 3)])
def test_floor_is_min_of_three_and_the_cap(monkeypatch, cap, expected):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", str(cap))
    b = _annotated_b()
    assert briefing._decision_floor(cap) == expected
    sel = _sel(b, 1)
    native = [d for d in b["decisions"] if not briefing._is_carried(d)]
    # at budget 1 only a floor of one survives; with room the floor is full
    base = _bytes(_sel(b, 1))
    roomy = _sel(b, base + 4000)
    kept_native = [d for d in roomy.kept["decisions"]
                   if not briefing._is_carried(d)]
    assert _texts(kept_native)[-expected:] == _texts(native[-expected:])
    assert len(sel.kept["decisions"]) == 1


def test_cap_and_budget_reasons_are_distinct(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    full = _bytes(_sel(b, None))
    sel = _sel(b, full - 900)
    caps = sel.dropped_for("decisions", "cap")
    assert len(caps) == 33
    note = briefing.section_note(sel, "decisions")
    assert "over the 10-item cap" in note
    assert "older not shown" in note
    sel2 = _sel(b, full - 1800)
    assert any(sel2.dropped_for(s, "budget") for s in briefing._ITEM_SECTIONS)
    assert len(sel2.dropped_for("decisions", "cap")) == 33


def test_large_handoff_at_the_body_floor(monkeypatch):
    # render_brief shrinks the body budget to 512 bytes under a huge
    # HANDOFF. The head is never cut; the rest degrades in the fixed order.
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    panel = ["Requests waiting on you (from other projects):",
             "→ q-aaaaaaaaaaaa  " + "do the thing " * 12,
             "  From: other",
             "→ q-bbbbbbbbbbbb  another ask",
             "  From: other"]
    sel = _sel(b, 512, rulings=RULINGS, request_lines=panel)
    text = briefing.render_selection(sel)
    assert text.splitlines()[0].startswith("While you were away")
    for line in RULINGS:
        assert line in text
    # panel collapsed to its header and a count line, floor down to one
    assert panel[0] in text and "→ q-aaaaaaaaaaaa" not in text
    assert "2 cut for budget, see: daimon request inbox" in text
    assert len(sel.kept["decisions"]) == 1
    assert sel.overage and "bytes over the 512-byte budget" in text


def test_panels_stay_whole_while_they_fit():
    b = _annotated_b()
    panel = ["Requests waiting on you (from other projects):",
             "→ q-aaaaaaaaaaaa  an ask", "  From: other"]
    sel = _sel(b, _bytes(_sel(b, None, request_lines=panel)),
               request_lines=panel)
    assert sel.panels == [panel]


def test_decision_cap_below_floor(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "2")
    b = _annotated_b()
    sel = _sel(b, None)
    assert len(sel.kept["decisions"]) == 2
    assert briefing._decision_floor(2) == 2


def test_flagged_stale_item_drops_last_and_is_announced():
    def mark(cp):
        loops = cp["working_context"]["open_questions"]
        target = next(i for i in loops if i["text"].startswith("ext-00"))
        target["_worldcheck"] = {"note": "#9 merged", "status": "merged"}
    b = _annotated_b(mutate=mark)
    flagged = next(i for i in b["external"] if i["text"].startswith("ext-00"))
    assert briefing._stale_days_of(flagged) is not None
    full = _bytes(_sel(b, None))
    seen_gone_note = False
    for budget in range(full, 400, -97):
        sel = _sel(b, budget)
        unflagged_stale_kept = [
            i for s in briefing._ITEM_SECTIONS for i in sel.kept[s]
            if briefing._stale_days_of(i) is not None
            and not briefing._is_flagged(i)]
        if flagged in sel.kept["external"] or flagged in sel.kept["open_loops"]:
            continue
        # the flagged one is gone: every unflagged candidate went first
        assert unflagged_stale_kept == []
        note = briefing.section_note(sel, "external")
        assert "1 flagged item hidden" in note
        seen_gone_note = True
        break
    assert seen_gone_note


def _external_notes(**kw):
    b = _annotated_b()
    full = _bytes(_sel(b, None))
    for budget in range(full, 400, -97):
        note = briefing.section_note(_sel(b, budget, **kw), "external")
        if note:
            yield note


def test_note_points_at_the_stale_listing_when_only_stale_items_were_lost():
    only_stale = [n for n in _external_notes() if " cut for budget" not in n]
    assert only_stale
    assert all(n.endswith("See: daimon loops --stale)") for n in only_stale)


def test_note_points_at_the_full_listing_once_fresh_items_were_cut_too():
    mixed = [n for n in _external_notes() if " cut for budget" in n]
    assert mixed
    assert all(n.endswith("See: daimon loops)") for n in mixed)


def test_note_has_no_pointer_when_the_route_is_not_the_listed_project():
    notes = list(_external_notes(loops_pointer=False))
    assert notes
    assert all("See:" not in n for n in notes)


def test_missing_and_future_stamps_are_not_stale():
    def strip(cp):
        loops = cp["working_context"]["open_questions"]
        loops[0].pop("last_verified")
        loops[0].pop("first_seen")
        loops[1]["last_verified"] = _iso(-9)   # nine days in the future
    b = _annotated_b(mutate=strip)
    by_text = {i["text"][:6]: i for i in b["external"]}
    assert briefing._stale_days_of(by_text["ext-00"]) is None
    assert briefing._stale_days_of(by_text["ext-01"]) is None
    full = _bytes(_sel(b, None))
    sel = _sel(b, full - 300)
    # never an error, and the unstamped ones are in the non-stale tier
    assert briefing.render_selection(sel)


def test_label_sections_survive_stage_one_truncation():
    labelled = ("**Problem:** the thing broke " + "x" * 300 + "\n"
                "**Fix:** reroute it " + "y" * 300)

    def add(cp):
        cp["epistemic_snapshot"]["strong_beliefs"][0]["text"] = labelled
        cp["epistemic_snapshot"]["strong_beliefs"][0]["importance"] = 10
    b = _annotated_b(mutate=add)
    full = _bytes(_sel(b, None))
    sel = _sel(b, full - 1)
    kept = [i for i in sel.kept["beliefs"] if "**Problem:**" in i["text"]]
    if kept:
        assert "**Fix:**" in kept[0]["text"]
        assert len(kept[0]["text"]) < len(labelled)
    # a verbatim item with labels is never rewritten, only kept or dropped
    cp = _fixture_checkpoint()
    cp["epistemic_snapshot"]["strong_beliefs"][0].update(
        text=labelled, trust="verbatim", quote="the quote", importance=10)
    b2 = _annotated_b(cp)
    sel2 = _sel(b2, _bytes(_sel(b2, None)) - 1)
    for i in sel2.kept["beliefs"]:
        if i["text"].startswith("**Problem:**"):
            assert i["text"] == labelled


def test_selection_is_a_pure_function_of_inputs(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    a = briefing.render_selection(_sel(b, 6000))
    c = briefing.render_selection(_sel(b, 6000))
    assert a == c


# ---- phase 3: render_plain / rich / teammates all go through select ----


def test_render_plain_is_a_thin_wrapper_over_select(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "6000")
    b = _annotated_b()
    assert briefing.render_plain(b, rulings=RULINGS) == \
        briefing.render_selection(
            briefing.select(b, briefing.effective_budget(), rulings=RULINGS))
    assert len(briefing.render_plain(b, rulings=RULINGS).encode()) <= 6000


def test_effective_budget_is_one_byte_unit(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "11264")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "3000")
    assert briefing.effective_budget() == 11264   # tokens*4 = 12000 is looser
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "800")
    assert briefing.effective_budget() == 3200
    assert briefing.effective_budget(1000) == 1000
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    assert briefing.effective_budget() is None


def test_plain_sections_follow_the_shared_order(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    text = briefing.render_plain(b)
    heads = [briefing.SECTION_HEADERS[s] for s in briefing.SECTION_ORDER
             if s in briefing.SECTION_HEADERS]
    positions = [text.index(h) for h in heads if h in text]
    assert positions == sorted(positions)
    assert text.index("Decisions made:") < text.index("VERIFY BEFORE TRUSTING")


def test_rich_path_uses_the_same_order_and_notes(monkeypatch, capsys):
    pytest.importorskip("rich")
    from daimon_briefing import render

    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "10")
    b = _annotated_b()
    render._rich_brief(b)
    out = capsys.readouterr().out
    assert out.index("Decisions made") < out.index("VERIFY BEFORE TRUSTING")
    assert "10 of 43 shown; 9 over the 10-item cap, 24 older not shown" in out


def test_teammate_blocks_carry_the_manifest_note(monkeypatch):
    from daimon_briefing import render

    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "2")
    b = _annotated_b()
    text = render._format_teammates([("grace", b)])
    assert "(2 of 43 shown; 17 over the 2-item cap, 24 older not shown)" in text
    assert "earlier decision" not in text


def test_build_keeps_every_decision_and_stays_pure(monkeypatch):
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "3")
    cp = _fixture_checkpoint()
    b = briefing.build(cp, now=NOW)
    assert len(b["decisions"]) == 43 and "decisions_overflow" not in b
    assert briefing.build(cp, now=NOW) == b
