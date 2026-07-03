#!/usr/bin/env python3
"""Tests for the state-tracking benchmark (M0.3)."""

import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate import LLMClient
from benchmark.state.scenarios import all_scenarios, Probe
from benchmark.state.grade import grade_state, answer_state, _mentions
from benchmark.state.memories import RawMemory, RagAppendMemory, CslMemory, SummaryMemory
from benchmark.state import run_state_benchmark as runner


def mock_client(answer_fn):
    c = LLMClient(api_key="t", cache_dir=tempfile.mkdtemp())
    c.delay = 0.0
    c._chat_completion_urllib = MagicMock(side_effect=lambda model, messages, **k: answer_fn(messages[0]["content"]))
    return c


# ---------------------------------------------------------------------------
# Scenario integrity — authored ground truth must be lexically sound
# ---------------------------------------------------------------------------

def test_scenarios_nonempty():
    scen = all_scenarios()
    assert len(scen) >= 3
    assert all(s.turns and s.probes for s in scen)


def test_override_probes_have_distinct_stale():
    for s in all_scenarios():
        for p in s.probes:
            if p.is_override:
                assert p.stale, f"{s.id}/{p.id} override but no stale values"
                # gold tokens must not collide with stale tokens (else grading is ambiguous)
                gold_l = {g.lower() for g in p.gold_terms()}
                stale_l = {x.lower() for x in p.stale}
                assert gold_l.isdisjoint(stale_l), f"{s.id}/{p.id} gold/stale overlap"


def test_each_scenario_has_at_least_one_override():
    for s in all_scenarios():
        assert any(p.is_override for p in s.probes), f"{s.id} has no override probe"


# ---------------------------------------------------------------------------
# Word-boundary matching
# ---------------------------------------------------------------------------

def test_mentions_word_boundary():
    assert _mentions("we switch to Go now", "Go")
    assert not _mentions("I use Google docs", "Go")      # no false positive inside a word
    assert _mentions("the cut is 20% this year", "20%")  # symbols match directly
    assert _mentions("Betoxil 75mg daily", "75mg")
    assert not _mentions("Betoxil 75mg daily", "50mg")


def test_mentions_identifier_style_tokens():
    # Structured memories leak snake_case identifiers into answers; these are
    # semantically the gold value and must match after separator normalization.
    assert _mentions("billing_revamp", "billing")
    assert _mentions("billing_revamp", "billing revamp")
    assert _mentions("usage_based", "usage-based")
    assert _mentions("Whitaker_Trust", "Whitaker")
    assert not _mentions("rebilling_revamp", "billing")  # boundaries still hold


# ---------------------------------------------------------------------------
# Deterministic grading
# ---------------------------------------------------------------------------

_PROBE = Probe("p", "lang?", gold="Rust", gold_aliases=["rust"], stale=["Go", "Golang"], is_override=True)


def test_grade_correct():
    g = grade_state("The current language is Rust.", _PROBE)
    assert g == {"correct": True, "has_gold": True, "stale": False}


def test_grade_stale_leak_fails():
    g = grade_state("They used Go, then moved to Rust.", _PROBE)
    assert g["has_gold"] and g["stale"] and not g["correct"]


def test_grade_missing_gold():
    g = grade_state("I don't know.", _PROBE)
    assert not g["has_gold"] and not g["correct"]


# ---------------------------------------------------------------------------
# Memory strategies
# ---------------------------------------------------------------------------

def test_raw_memory_accumulates():
    m = RawMemory()
    for t in ["a", "b", "c"]:
        m.observe(t)
    ctx = m.context("q")
    assert "a" in ctx and "c" in ctx


def test_rag_append_budget_bounded():
    m = RagAppendMemory(budget=30)
    for i in range(40):
        m.observe(f"Speaker {i}: detail about topic_{i} number {i}")
    assert m.tokens() == 30
    ctx = m.context("topic_5")
    from benchmark.evaluate import count_tokens
    assert count_tokens(ctx) <= 30 + 20  # within budget + one chunk overshoot


def test_csl_and_summary_update_on_observe():
    c = mock_client(lambda prompt: "FACT(state=updated)")
    for Mem in (CslMemory, SummaryMemory):
        m = Mem(c, model="x", budget=100)
        assert m.tokens() == 0
        m.observe("User: something changed")
        assert "updated" in m.memory


def test_consolidating_memory_keeps_old_on_empty_output():
    c = mock_client(lambda prompt: "")   # model returns nothing
    m = CslMemory(c, model="x", budget=100)
    m.memory = "FACT(keep=true)"
    m.observe("turn")
    assert m.memory == "FACT(keep=true)"  # not wiped by an empty response


# Reasoning-leak sanitization: kimi-k2.6 intermittently prefixes
# chain-of-thought to its completion. The CSL prompt demands "ONLY CSL
# statements" — enforce that contract by keeping only TYPE(...) lines.
# This is structure's actual advantage: DSL output is validatable, prose isn't.

_LEAKY_OUTPUT = """The user wants me to update a compact memory written in CSL.

 Current memory:
 - FACT(user, preparing, lab_grant_application)

 Wait, I need to think about what changed here.

FACT(lab_grant_application, target, Whitaker_Trust)
- PREFERENCE(user, prefers, morning_meetings)
INTENT(user, complete, lab_grant_application)
That should cover the update."""


def test_csl_sanitizes_reasoning_leak():
    c = mock_client(lambda prompt: _LEAKY_OUTPUT)
    m = CslMemory(c, model="x", budget=100)
    m.observe("turn")
    lines = m.memory.splitlines()
    assert "FACT(lab_grant_application, target, Whitaker_Trust)" in lines
    assert "PREFERENCE(user, prefers, morning_meetings)" in lines  # bullet stripped
    assert "INTENT(user, complete, lab_grant_application)" in lines
    # statements quoted inside the reasoning's "Current memory" echo still count
    assert "FACT(user, preparing, lab_grant_application)" in lines
    assert not any("Wait" in l or "user wants" in l or "cover the update" in l
                   for l in lines)


def test_csl_keeps_old_memory_when_nothing_parseable():
    c = mock_client(lambda prompt: "I could not produce the memory, sorry.")
    m = CslMemory(c, model="x", budget=100)
    m.memory = "FACT(keep=true)"
    m.observe("turn")
    assert m.memory == "FACT(keep=true)"


def test_summary_does_not_sanitize():
    # Prose has no validatable format — the summary arm stores output as-is.
    c = mock_client(lambda prompt: "The user is preparing a grant application.")
    m = SummaryMemory(c, model="x", budget=100)
    m.observe("turn")
    assert m.memory == "The user is preparing a grant application."


def test_answer_state_empty_memory():
    c = mock_client(lambda prompt: "should not matter")
    assert "no memory" in answer_state("", "q?", c).lower()


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------

def _fake_probe_result(correct, override, stale=False, has_gold=None):
    return {"probe": "x", "is_override": override, "gold": "g",
            "answer": "", "correct": correct,
            "has_gold": correct if has_gold is None else has_gold, "stale": stale}


def test_aggregate_override_and_staleness():
    sr = [{
        "scenario": "s1", "domain": "d",
        "methods": {
            "csl": {"context_tokens": 100, "probes": [
                _fake_probe_result(True, True), _fake_probe_result(True, True),
                _fake_probe_result(True, False)]},
            "summary": {"context_tokens": 100, "probes": [
                _fake_probe_result(False, True, stale=True), _fake_probe_result(True, True),
                _fake_probe_result(True, False)]},
        },
    }]
    agg = runner.aggregate(sr)
    assert agg["csl"]["override_accuracy"] == pytest.approx(1.0)
    assert agg["summary"]["override_accuracy"] == pytest.approx(0.5)
    assert agg["summary"]["staleness_rate"] == pytest.approx(0.5)
    assert agg["csl"]["staleness_rate"] == pytest.approx(0.0)


def test_report_has_verdict():
    agg = {
        "csl": {"overall_accuracy": 0.9, "gold_recall": 0.9, "override_accuracy": 0.8,
                "staleness_rate": 0.1, "mean_context_tokens": 100, "n_probes": 10, "n_override_probes": 5},
        "summary": {"overall_accuracy": 0.7, "gold_recall": 0.7, "override_accuracy": 0.5,
                    "staleness_rate": 0.3, "mean_context_tokens": 100, "n_probes": 10, "n_override_probes": 5},
    }
    rep = runner.make_report(agg)
    assert "State-Tracking Benchmark" in rep
    assert "CSL beats prose summary" in rep


# ---------------------------------------------------------------------------
# Mocked end-to-end
# ---------------------------------------------------------------------------

def test_end_to_end_mocked(tmp_path):
    # Answer mock: per-question gold lookup. A single kitchen-sink string can't
    # work anymore — swap and sibling-preservation probes are anti-correlated
    # (the correct answer for one probe is a stale token for another).
    gold_by_question = {p.question: p.gold
                        for s in all_scenarios() for p in s.probes}

    def fake(prompt):
        if "CURRENT-STATE ANSWER:" in prompt:
            q = re.search(r"QUESTION: (.+)", prompt).group(1).strip()
            return gold_by_question[q]
        return "FACT(state=consolidated)"  # update calls

    with patch.object(LLMClient, "chat_completion",
                      side_effect=lambda model, messages, **k: fake(messages[0]["content"])):
        res = runner.run(str(tmp_path), update_model="x", answer_model="x", budget=120)

    agg = res["aggregate"]
    assert set(agg.keys()) == {"raw", "csl", "summary", "rag-append"}
    for m in agg.values():
        assert m["overall_accuracy"] == pytest.approx(1.0)
        assert m["staleness_rate"] in (0.0, None)
    report = (tmp_path / "report.md").read_text()
    assert "Decisive" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
