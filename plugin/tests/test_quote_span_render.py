"""#1129 PR B: the evidence quote beside an item is a bounded span, hidden
when the item's text already says it, and identical on every render path."""

import pytest

from daimon_briefing import briefing, display

from .test_briefing_select import NOW, _annotated_b


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "0")
    monkeypatch.setenv("DAIMON_STALE_DAYS", "7")


LONG = " ".join(f"word{n}" for n in range(80))


def _item(**kw):
    return {"id": "d-000001", "text": "we chose uv", "trust": "verbatim", **kw}


def _checkpoint(decisions):
    return {"session_id": "S",
            "working_context": {"open_questions": [],
                                "recent_decisions": decisions},
            "epistemic_snapshot": {}}


def test_line_caps_a_long_quote():
    line = briefing._line(_item(quote=LONG))
    span = display.quote_span(LONG, "we chose uv")
    assert f'  — "{span}"' in line
    assert span.endswith("…") and LONG not in line


def test_line_hides_a_quote_the_text_already_contains():
    line = briefing._line(_item(text="we chose uv for installs",
                                quote="chose uv"))
    assert "—" not in line and '"' not in line


def test_line_short_unseen_quote_unchanged():
    assert briefing._line(_item(quote="uv, always")).endswith(
        '  — "uv, always"')


def test_line_is_null_safe():
    line = briefing._line(_item(text=None, quote=None))
    assert line.startswith("- [") and "—" not in line


def test_every_trust_class_gets_the_same_rule():
    for trust in ("verbatim", "inferred"):
        line = briefing._line(_item(trust=trust, quote=LONG))
        assert LONG not in line and "…" in line


def test_truncating_text_never_brings_a_quote_back():
    quote = "pin the version to 3.12 exactly"
    pad = " ".join(f"pad{n}" for n in range(150))
    b = _annotated_b(_checkpoint([
        {"id": "d-000001", "text": f"{pad} {quote} {pad}",
         "trust": "inferred", "quote": quote,
         "first_seen": "2026-01-01T00:00:00Z", "importance": 5}]))
    full_text = briefing.render_selection(briefing.select(b, None, NOW))
    assert f'— "{quote}"' not in full_text
    # tight budget: stage 1 shortens the text, which no longer holds the quote
    sel = briefing.select(b, len(full_text.encode()) - 800, NOW)
    kept = sel.kept["decisions"][0]
    assert len(kept["text"]) < len(b["decisions"][0]["text"])
    assert quote not in kept["text"]
    assert f'— "{quote}"' not in briefing.render_selection(sel)


def test_plain_and_rich_show_the_same_span(monkeypatch, capsys):
    pytest.importorskip("rich")
    from daimon_briefing import render
    monkeypatch.setenv("COLUMNS", "400")
    b = {"decisions": [_item(quote=LONG)]}
    span = display.quote_span(LONG, "we chose uv")
    assert f'  — "{span}"' in briefing.render_plain(b)
    render._rich_brief(b)
    assert f'"{span}"' in capsys.readouterr().out


def test_rich_panel_survives_null_text_and_quote(capsys):
    pytest.importorskip("rich")
    from daimon_briefing import render
    render._rich_brief({
        "decisions": [{"id": "d-1", "text": None, "quote": None,
                       "trust": "inferred"}],
        "open_loops": [{"id": "l-1", "text": None, "quote": None,
                        "trust": "inferred", "_stale_carried_days": 9}]})
    assert "•" in capsys.readouterr().out


def test_rich_hides_a_contained_quote(monkeypatch, capsys):
    pytest.importorskip("rich")
    from daimon_briefing import render
    monkeypatch.setenv("COLUMNS", "400")
    render._rich_brief({"decisions": [_item(text="we chose uv for installs",
                                            quote="chose uv")]})
    assert '"chose uv"' not in capsys.readouterr().out


# ---- the LLM path charges the full quote ----

def _quote_heavy_b():
    return _annotated_b(_checkpoint([
        {"id": f"d-{n:06x}", "text": f"decision {n}", "trust": "verbatim",
         "quote": LONG, "first_seen": "2026-01-01T00:00:00Z",
         "importance": 5} for n in range(4)]))


def test_full_quotes_charges_the_whole_quote():
    b = _quote_heavy_b()
    capped = briefing.render_selection(briefing.select(b, None, NOW))
    whole = briefing.render_selection(
        briefing.select(b, None, NOW, full_quotes=True))
    assert LONG in whole and LONG not in capped
    assert len(whole.encode()) - len(capped.encode()) \
        >= 4 * (len(LONG) - display.QUOTE_CHARS)


def test_full_quotes_changes_what_a_budget_keeps():
    b = _quote_heavy_b()
    budget = len(briefing.render_selection(
        briefing.select(b, None, NOW)).encode()) + 50
    assert len(briefing.select(b, budget, NOW).kept["decisions"]) == 4
    assert len(briefing.select(b, budget, NOW, full_quotes=True)
               .kept["decisions"]) < 4


def test_llm_render_sizes_with_full_quotes_and_falls_back_deterministic(
        monkeypatch):
    from daimon_briefing import llm
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    seen = []
    real = briefing.select

    def spy(*a, **kw):
        seen.append(kw.get("full_quotes", False))
        return real(*a, **kw)
    monkeypatch.setattr(briefing, "select", spy)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "narrative, no quotes")
    out = briefing.render(_checkpoint([
        {"id": "d-000001", "text": "decision", "trust": "verbatim",
         "quote": LONG}]))
    # the LLM sizing charges whole quotes; the fallback uses the span model
    assert seen == [True, False]
    # validation failed (quote lost): deterministic fallback, capped quote
    assert out and not out.startswith("narrative")
    assert LONG not in out and "…" in out


# ---- the decide amendment card wraps the quote, never cuts it ----

def _amend_row(evidence, aid="a-abcabcabcabc"):
    return {"kind": "amendment", "id": aid, "headline": "h",
            "waiting_since": "",
            "commands": [("confirm", f"daimon amend ratify {aid}")],
            "amend": {"loop_id": "o-1", "loop_text": "ship it",
                      "state_from": "open", "state_to": "progressed",
                      "evidence": evidence, "found": "in a user turn",
                      "note": ""}}


def _quote_block(card):
    start = next(n for n, ln in enumerate(card) if ln.startswith("  quote  "))
    end = next(n for n, ln in enumerate(card) if ln.startswith("  found"))
    return card[start:end]


def test_decide_card_wraps_a_long_quote_without_cutting_it():
    from daimon_briefing.cli import lifecycle
    block = _quote_block(lifecycle._amendment_card(_amend_row(LONG)))
    assert len(block) > 1
    assert all(len(ln) <= 76 for ln in block)
    assert block[0].startswith('  quote  "word0 ')
    assert all(ln.startswith(" " * 11) for ln in block[1:])
    assert block[-1].endswith('word79"')
    joined = " ".join(ln.strip() for ln in block)
    assert joined.replace('quote  "', "").rstrip('"').split() == LONG.split()


def test_decide_fanout_card_wraps_the_quote_too():
    from daimon_briefing.cli import lifecycle
    cards = lifecycle._decide_cards([_amend_row(LONG, "a-aaaaaaaaaaaa"),
                                     _amend_row(LONG, "a-bbbbbbbbbbbb")])
    assert len(cards) == 1
    block = _quote_block(cards[0])
    assert len(block) > 1 and all(len(ln) <= 76 for ln in block)
    assert block[-1].endswith('word79"')


def test_decide_card_short_quote_keeps_its_one_line():
    from daimon_briefing.cli import lifecycle
    card = lifecycle._amendment_card(_amend_row("the PR merged"))
    assert '  quote  "the PR merged"' in card
