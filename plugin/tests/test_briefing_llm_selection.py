"""#1128 phase 5: the opt-in LLM briefing narrates only what `select` kept,
and the verbatim-quote check runs over that same set."""

import json

import pytest

from daimon_briefing import briefing, llm

from .test_briefing_select import _fixture_checkpoint


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "0")
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "3")


def _stub(monkeypatch, reply):
    seen = {}

    def fake_chat(messages, **kw):
        seen["checkpoint"] = json.loads(messages[1]["content"].split("CHECKPOINT:\n", 1)[1])
        return reply(seen["checkpoint"])
    monkeypatch.setattr(llm, "chat", fake_chat)
    return seen


def _quotes(cp):
    return [i["quote"].strip()
            for i in cp["working_context"]["open_questions"] if i.get("quote")]


def test_llm_sees_only_the_selected_items(monkeypatch):
    seen = _stub(monkeypatch, lambda cp: "narrative " + " ".join(_quotes(cp)))
    out = briefing.render(_fixture_checkpoint())
    assert out and out.startswith("narrative")
    cp = seen["checkpoint"]
    decisions = cp["working_context"]["recent_decisions"]
    assert len(decisions) == 3                        # the cap, not 43
    assert all(not d.get("carried_from") for d in decisions)
    assert len(cp["working_context"]["open_questions"]) == 24
    assert cp["working_context"]["active_topic"]["text"] == "the active topic"


def test_llm_input_shrinks_with_the_budget(monkeypatch):
    seen = _stub(monkeypatch, lambda cp: "narrative " + " ".join(_quotes(cp)))
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "2600")
    briefing.render(_fixture_checkpoint())
    cp = seen["checkpoint"]
    kept = (len(cp["working_context"]["open_questions"])
            + len(cp["epistemic_snapshot"]["uncertainties"])
            + len(cp["epistemic_snapshot"]["strong_beliefs"]))
    assert kept < 24 + 24 + 10


def test_quote_validation_runs_over_the_kept_set(monkeypatch):
    # A quote that was cut by the budget is not the LLM's to keep: only the
    # quotes of the items it was shown are checked.
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "2600")
    cp_full = _fixture_checkpoint()
    all_quotes = [i["quote"].strip()
                  for i in cp_full["working_context"]["open_questions"]
                  if i.get("quote")]
    seen = _stub(monkeypatch, lambda cp: "narrative " + " ".join(_quotes(cp)))
    out = briefing.render(cp_full)
    shown = _quotes(seen["checkpoint"])
    assert len(shown) < len(all_quotes)               # the budget cut some
    assert out and out.startswith("narrative")        # and that is not a failure


def test_losing_a_shown_quote_still_falls_back(monkeypatch):
    seen = _stub(monkeypatch, lambda cp: "narrative with every quote lost")
    out = briefing.render(_fixture_checkpoint())
    assert seen["checkpoint"]
    assert out and not out.startswith("narrative")    # deterministic render
    assert "Decisions made:" in out
