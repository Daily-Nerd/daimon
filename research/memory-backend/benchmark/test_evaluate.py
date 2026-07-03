#!/usr/bin/env python3
"""
Unit tests for the Context-as-Program benchmark evaluation pipeline.

Run with:
    pytest benchmark/test_evaluate.py -v
    python -m pytest benchmark/test_evaluate.py -v

Note: LLMClient calls the proxy via _chat_completion_urllib() (stdlib urllib),
so the mocks patch that seam rather than an SDK client object.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate import (
    LLMClient,
    CompressionResult,
    Question,
    Answer,
    Score,
    QAItem,
    ConversationResult,
    BenchmarkResult,
    count_tokens,
    extract_primitives,
    measure_compression,
    generate_questions,
    answer_from_raw,
    answer_from_csl,
    answer_from_rag,
    grade_answers,
    grade_answer_against_source,
    chunk_conversation,
    retrieve_context,
    generate_report,
    _adjust_params,
    aggregate_compression_results,
    aggregate_qa_results,
    aggregate_qa_by_method,
    _safe_json_loads,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client_with_mock(response_text: str, cache_dir: str | None = None):
    """Return an LLMClient whose proxy call is mocked to return response_text."""
    client = LLMClient(api_key="test-key", cache_dir=cache_dir or tempfile.mkdtemp())
    client.delay = 0.0
    client._chat_completion_urllib = MagicMock(return_value=response_text)
    return client


def make_failing_client():
    """Return an LLMClient whose proxy call always raises."""
    client = LLMClient(api_key="test-key", cache_dir=tempfile.mkdtemp())
    client.delay = 0.0
    client._chat_completion_urllib = MagicMock(side_effect=Exception("API down"))
    return client


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def test_count_tokens_non_empty():
    text = "Hello world, this is a test of the token counter."
    tokens = count_tokens(text)
    assert tokens > 0


def test_count_tokens_empty():
    # Empty string is zero tokens (the function short-circuits before max(1, ...)).
    assert count_tokens("") == 0


def test_count_tokens_longer_text_has_more_tokens():
    short = "Hello."
    long = "Hello " * 100
    assert count_tokens(long) > count_tokens(short)


# ---------------------------------------------------------------------------
# Primitive extraction
# ---------------------------------------------------------------------------

def test_extract_primitives_basic():
    csl = """
FACT(id="F1", subject="A", predicate="b", object="c")
RELATION(entity1="X", type="knows", entity2="Y")
PREFERENCE(actor="User", domain="lang", value="go")
EVENT(id="E1", timestamp="2024-01-01T00:00:00Z", type="meeting")
INTENT(actor="User", goal="migrate")
UNRESOLVED(question="What?")
SUMMARY(scope="Q1", theme="test", key_points=["a"])
RULE(trigger="t", action="a")
NOTE(content="hello")
"""
    counts = extract_primitives(csl)
    assert counts["FACT"] == 1
    assert counts["RELATION"] == 1
    assert counts["PREFERENCE"] == 1
    assert counts["EVENT"] == 1
    assert counts["INTENT"] == 1
    assert counts["UNRESOLVED"] == 1
    assert counts["SUMMARY"] == 1
    assert counts["RULE"] == 1
    assert counts["NOTE"] == 1


def test_extract_primitives_multiple():
    csl = "FACT(...)\nFACT(...)\nRELATION(...)"
    counts = extract_primitives(csl)
    assert counts["FACT"] == 2
    assert counts["RELATION"] == 1


def test_extract_primitives_none():
    counts = extract_primitives("just some random text")
    assert counts == {}


# ---------------------------------------------------------------------------
# Compression measurement
# ---------------------------------------------------------------------------

def test_measure_compression_basic():
    raw = "This is a fairly long conversation with many words and details. " * 50
    csl = 'FACT(id="F1", subject="Project", predicate="status", object="active")\nPREFERENCE(actor="User", domain="lang", value="go")'
    result = measure_compression(raw, csl)
    assert result.raw_tokens > 0
    assert result.csl_tokens > 0
    assert result.ratio > 0.0
    assert "FACT" in result.primitives
    assert "PREFERENCE" in result.primitives


def test_measure_compression_empty_csl():
    raw = "Some conversation text here."
    result = measure_compression(raw, "")
    assert result.csl_tokens == count_tokens("")
    assert result.ratio == 0.0
    assert result.primitives == {}


# ---------------------------------------------------------------------------
# JSON safe loader
# ---------------------------------------------------------------------------

def test_safe_json_loads_direct():
    assert _safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_array():
    assert _safe_json_loads('[1, 2, 3]') == [1, 2, 3]


def test_safe_json_loads_markdown():
    text = "```json\n{\"x\": 42}\n```"
    assert _safe_json_loads(text) == {"x": 42}


def test_safe_json_loads_extra_text():
    text = "Here is the result:\n```\n{\"y\": 99}\n```\nHope that helps!"
    assert _safe_json_loads(text) == {"y": 99}


def test_safe_json_loads_invalid():
    assert _safe_json_loads("not json at all") is None


def test_safe_json_loads_empty():
    assert _safe_json_loads("") is None


# ---------------------------------------------------------------------------
# LLMClient caching / rate limiting / retries (urllib seam)
# ---------------------------------------------------------------------------

def test_llm_client_cache_hit():
    with tempfile.TemporaryDirectory() as tmpdir:
        client = make_client_with_mock("cached response", cache_dir=tmpdir)
        messages = [{"role": "user", "content": "hello"}]
        # First call writes cache
        result1 = client.chat_completion("gpt-test", messages)
        assert result1 == "cached response"
        assert client._chat_completion_urllib.call_count == 1

        # Second call should hit cache (no additional proxy call)
        result2 = client.chat_completion("gpt-test", messages)
        assert result2 == "cached response"
        assert client._chat_completion_urllib.call_count == 1


def test_llm_client_rate_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        client = make_client_with_mock("ok", cache_dir=tmpdir)
        client.delay = 0.2
        t0 = time.time()
        client.chat_completion("m", [{"role": "user", "content": "a"}])
        client.chat_completion("m", [{"role": "user", "content": "b"}])
        elapsed = time.time() - t0
        # Should have at least one delay between calls
        assert elapsed >= 0.15


def test_llm_client_retry_then_fail():
    with tempfile.TemporaryDirectory() as tmpdir:
        client = LLMClient(api_key="test-key", cache_dir=tmpdir)
        client.delay = 0.0
        client._chat_completion_urllib = MagicMock(side_effect=Exception("API down"))
        result = client.chat_completion("m", [{"role": "user", "content": "test"}], retries=2)
        assert result is None
        assert client._chat_completion_urllib.call_count == 2


def test_llm_client_timeout_fails_fast_no_retry():
    # A read timeout means the model stalled; retrying burns another full 300s
    # for the same result (the ornith failure mode). Fail fast, do not retry.
    with tempfile.TemporaryDirectory() as tmpdir:
        client = LLMClient(api_key="test-key", cache_dir=tmpdir)
        client.delay = 0.0
        client._chat_completion_urllib = MagicMock(side_effect=TimeoutError("stalled"))
        result = client.chat_completion("m", [{"role": "user", "content": "test"}], retries=3)
        assert result is None
        assert client._chat_completion_urllib.call_count == 1


def test_llm_client_urlerror_wrapping_timeout_fails_fast():
    import urllib.error
    with tempfile.TemporaryDirectory() as tmpdir:
        client = LLMClient(api_key="test-key", cache_dir=tmpdir)
        client.delay = 0.0
        client._chat_completion_urllib = MagicMock(
            side_effect=urllib.error.URLError(TimeoutError("timed out")))
        result = client.chat_completion("m", [{"role": "user", "content": "test"}], retries=3)
        assert result is None
        assert client._chat_completion_urllib.call_count == 1


def test_llm_client_nontimeout_urlerror_still_retries():
    # Genuine connection errors (port-forward down) are transient → keep retrying.
    import urllib.error
    with tempfile.TemporaryDirectory() as tmpdir:
        client = LLMClient(api_key="test-key", cache_dir=tmpdir)
        client.delay = 0.0
        client._chat_completion_urllib = MagicMock(
            side_effect=urllib.error.URLError("connection refused"))
        result = client.chat_completion("m", [{"role": "user", "content": "test"}], retries=3)
        assert result is None
        assert client._chat_completion_urllib.call_count == 3


def test_llm_client_preflight_rejects_oversized(monkeypatch):
    # LITELLM_CONTEXT_WINDOW set + prompt that won't fit → skip BEFORE any call.
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "10")
    with tempfile.TemporaryDirectory() as tmpdir:
        client = make_client_with_mock("never reached", cache_dir=tmpdir)
        result = client.chat_completion(
            "m", [{"role": "user", "content": "word " * 1000}], max_tokens=100)
        assert result is None
        assert client._chat_completion_urllib.call_count == 0  # no network


def test_llm_client_preflight_allows_within_window(monkeypatch):
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "100000")
    with tempfile.TemporaryDirectory() as tmpdir:
        client = make_client_with_mock("ok", cache_dir=tmpdir)
        result = client.chat_completion("m", [{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert client._chat_completion_urllib.call_count == 1


def test_llm_client_malformed_window_ignored(monkeypatch):
    # Garbage env must not crash a run — gate simply off.
    monkeypatch.setenv("LITELLM_CONTEXT_WINDOW", "not-a-number")
    with tempfile.TemporaryDirectory() as tmpdir:
        client = make_client_with_mock("ok", cache_dir=tmpdir)
        result = client.chat_completion("m", [{"role": "user", "content": "word " * 1000}])
        assert result == "ok"
        assert client._chat_completion_urllib.call_count == 1


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

def test_generate_questions_success():
    fake_json = json.dumps([
        {"id": "q1", "text": "What is the budget?", "category": "fact"},
        {"id": "q2", "text": "Who prefers Go?", "category": "preference"},
    ])
    client = make_client_with_mock(fake_json)
    questions = generate_questions("some conversation", n=2, llm_client=client)
    assert len(questions) == 2
    assert questions[0].text == "What is the budget?"
    assert questions[1].category == "preference"


def test_generate_questions_api_failure():
    client = make_failing_client()
    with pytest.raises(RuntimeError, match="Failed to generate questions"):
        generate_questions("conversation", n=3, llm_client=client)


def test_generate_questions_bad_json():
    client = make_client_with_mock("not json")
    with pytest.raises(RuntimeError, match="Failed to parse questions JSON"):
        generate_questions("conversation", n=3, llm_client=client)


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

def test_answer_from_raw_success():
    client = make_client_with_mock("The budget is $2.4M.")
    ans = answer_from_raw("What is the budget?", "long conversation...", llm_client=client)
    assert ans.text == "The budget is $2.4M."
    assert ans.source == "raw"


def test_answer_from_raw_failure():
    client = make_failing_client()
    ans = answer_from_raw("What?", "conv", llm_client=client)
    assert ans.text.startswith("[ERROR:")
    assert ans.source == "raw"


def test_answer_from_csl_success():
    client = make_client_with_mock("They prefer Go.")
    ans = answer_from_csl("Language preference?", 'PREFERENCE(...)', llm_client=client)
    assert ans.text == "They prefer Go."
    assert ans.source == "csl"


# ---------------------------------------------------------------------------
# RAG baseline: chunking + retrieval at equal token budget
# ---------------------------------------------------------------------------

def test_chunk_conversation_splits_and_packs():
    conv = "\n".join(f"Speaker {i}: line number {i} with some content" for i in range(20))
    chunks = chunk_conversation(conv, max_chunk_tokens=20)
    assert len(chunks) > 1
    # No chunk should be wildly over budget (allow one line's overshoot)
    assert all(count_tokens(c) <= 20 + count_tokens("Speaker 19: line number 19 with some content")
               for c in chunks)


def test_chunk_conversation_skips_blank_lines():
    conv = "Alex: hello\n\n\nJordan: hi there\n"
    chunks = chunk_conversation(conv, max_chunk_tokens=100)
    joined = "\n".join(chunks)
    assert "hello" in joined and "hi there" in joined
    assert "\n\n\n" not in joined


def test_retrieve_context_respects_budget():
    chunks = [f"chunk {i} about topic_{i} " * 5 for i in range(10)]
    budget = 30
    ctx = retrieve_context("topic_3 topic_7", chunks, token_budget=budget)
    assert ctx != ""
    assert count_tokens(ctx) <= budget + count_tokens(chunks[0])


def test_retrieve_context_relevance():
    chunks = [
        "The budget was cut by thirty percent this quarter.",
        "The team prefers Go over Python for new services.",
        "Lunch is at noon in the cafeteria.",
    ]
    ctx = retrieve_context("What language does the team prefer?", chunks, token_budget=50)
    assert "Go" in ctx


def test_retrieve_context_empty_inputs():
    assert retrieve_context("anything", [], token_budget=100) == ""
    assert retrieve_context("anything", ["some chunk"], token_budget=0) == ""


def test_answer_from_rag_success():
    client = make_client_with_mock("The team prefers Go.")
    # Question shares vocabulary with the conversation so lexical retrieval hits.
    conv = "Alex: We use Go.\nJordan: Yes, Go over Python for sure."
    ans = answer_from_rag("What does Jordan say about Python?", conv, token_budget=50, llm_client=client)
    assert ans.text == "The team prefers Go."
    assert ans.source == "rag"


def test_answer_from_rag_no_context_returns_dont_know():
    client = make_client_with_mock("should not be called")
    ans = answer_from_rag("question with zero overlap zzz", "", token_budget=50, llm_client=client)
    assert ans.source == "rag"
    assert "don't know" in ans.text.lower()


# ---------------------------------------------------------------------------
# Grading: legacy two-way (deprecated) + blind source-grounded
# ---------------------------------------------------------------------------

def test_grade_answers_success():
    fake_grade = json.dumps({
        "accuracy": 0.9,
        "completeness": 0.8,
        "tone_match": 0.85,
        "explanation": "Good but missed one detail.",
    })
    client = make_client_with_mock(fake_grade)
    score = grade_answers("raw ans", "csl ans", "question", llm_client=client)
    assert score.accuracy == pytest.approx(0.9)
    assert score.completeness == pytest.approx(0.8)
    assert score.tone_match == pytest.approx(0.85)
    assert score.overall == pytest.approx((0.9 + 0.8 + 0.85) / 3)
    assert "detail" in score.explanation


def test_grade_answers_api_failure():
    client = make_failing_client()
    score = grade_answers("raw", "csl", "q", llm_client=client)
    assert score.accuracy == 0.0
    assert score.explanation.startswith("[ERROR:")


def test_grade_answers_bad_json():
    client = make_client_with_mock("not json")
    score = grade_answers("raw", "csl", "q", llm_client=client)
    assert score.accuracy == 0.0
    assert "JSON parse failure" in score.explanation


def test_grade_answer_against_source_success():
    fake = json.dumps({
        "accuracy": 0.9, "completeness": 0.7, "tone_match": 0.8,
        "explanation": "Accurate per source, missed one detail.",
    })
    client = make_client_with_mock(fake)
    score = grade_answer_against_source("the answer", "the question", "the source conv", llm_client=client)
    assert score.accuracy == pytest.approx(0.9)
    assert score.completeness == pytest.approx(0.7)
    assert score.overall == pytest.approx((0.9 + 0.7 + 0.8) / 3)


def test_grade_answer_against_source_bad_json():
    client = make_client_with_mock("not json")
    score = grade_answer_against_source("a", "q", "conv", llm_client=client)
    assert score.accuracy == 0.0
    assert "JSON parse failure" in score.explanation


def test_grade_answer_against_source_api_failure():
    client = make_failing_client()
    score = grade_answer_against_source("a", "q", "conv", llm_client=client)
    assert score.accuracy == 0.0
    assert score.explanation.startswith("[ERROR:")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_aggregate_compression_results():
    convs = [
        ConversationResult("c1", "se", CompressionResult(1000, 100, 10.0, {"FACT": 2})),
        ConversationResult("c2", "se", CompressionResult(2000, 200, 10.0, {"FACT": 1, "RULE": 1})),
    ]
    agg = aggregate_compression_results(convs)
    assert agg["mean_ratio"] == 10.0
    assert agg["total_raw_tokens"] == 3000
    assert agg["total_csl_tokens"] == 300
    assert agg["primitive_totals"]["FACT"] == 3
    assert agg["primitive_totals"]["RULE"] == 1


def test_aggregate_qa_results():
    convs = [
        ConversationResult(
            "c1", "se", CompressionResult(100, 10, 10.0, {}),
            qa_items=[
                QAItem(Question("q1", "a", "fact"), Answer("r", "raw"), Answer("c", "csl"), Score(1.0, 1.0, 1.0, 1.0, "perfect")),
                QAItem(Question("q2", "b", "fact"), Answer("r", "raw"), Answer("c", "csl"), Score(0.5, 0.5, 0.5, 0.5, "meh")),
            ],
        ),
    ]
    agg = aggregate_qa_results(convs)
    assert agg["mean_accuracy"] == pytest.approx(0.75)
    assert agg["mean_completeness"] == pytest.approx(0.75)
    assert agg["mean_tone_match"] == pytest.approx(0.75)
    assert agg["mean_overall"] == pytest.approx(0.75)
    assert agg["total_questions"] == 2


def test_aggregate_qa_results_empty():
    assert aggregate_qa_results([])["mean_overall"] == 0.0


def test_qaitem_backcompat_positional():
    # Old four-arg construction must still work (no per-method scores).
    item = QAItem(Question("q1", "?", "fact"), Answer("r", "raw"), Answer("c", "csl"),
                  Score(1.0, 1.0, 1.0, 1.0, "ok"))
    assert item.rag_answer is None and item.rag_score is None
    assert aggregate_qa_by_method([ConversationResult("c1", "se",
        CompressionResult(100, 10, 10.0, {}), qa_items=[item])]) == {}


def test_aggregate_qa_by_method():
    raw_s = Score(0.6, 0.6, 0.6, 0.6, "raw ok")
    csl_s = Score(0.9, 0.9, 0.9, 0.9, "csl ok")
    rag_s = Score(0.5, 0.5, 0.5, 0.5, "rag ok")
    item = QAItem(Question("q1", "?", "fact"), Answer("r", "raw"), Answer("c", "csl"),
                  csl_s, rag_answer=Answer("g", "rag"),
                  raw_score=raw_s, csl_score=csl_s, rag_score=rag_s)
    by_method = aggregate_qa_by_method([ConversationResult("c1", "se",
        CompressionResult(100, 10, 10.0, {}), qa_items=[item])])
    assert by_method["raw"]["mean_overall"] == pytest.approx(0.6)
    assert by_method["csl"]["mean_overall"] == pytest.approx(0.9)
    assert by_method["rag"]["mean_overall"] == pytest.approx(0.5)
    assert by_method["csl"]["n"] == 1


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def test_generate_report_basic():
    result = BenchmarkResult(
        conversations=[
            ConversationResult(
                "c1", "se", CompressionResult(1000, 100, 10.0, {"FACT": 2}),
                qa_items=[
                    QAItem(
                        Question("q1", "What?", "fact"),
                        Answer("raw", "raw"), Answer("csl", "csl"),
                        Score(1.0, 1.0, 1.0, 1.0, "good")
                    ),
                ],
            ),
        ],
        aggregate_compression=aggregate_compression_results([
            ConversationResult("c1", "se", CompressionResult(1000, 100, 10.0, {"FACT": 2}))
        ]),
        aggregate_qa=aggregate_qa_results([
            ConversationResult(
                "c1", "se", CompressionResult(1000, 100, 10.0, {"FACT": 2}),
                qa_items=[
                    QAItem(
                        Question("q1", "What?", "fact"),
                        Answer("raw", "raw"), Answer("csl", "csl"),
                        Score(1.0, 1.0, 1.0, 1.0, "good")
                    ),
                ],
            ),
        ]),
    )
    report = generate_report(result)
    assert "# Context-as-Program Benchmark Report" in report
    assert "c1" in report
    assert "10.00x" in report
    assert "Best" in report
    assert "Worst" in report


def test_generate_report_with_failures():
    result = BenchmarkResult(
        conversations=[
            ConversationResult(
                "c1", "se", CompressionResult(100, 10, 10.0, {}),
                qa_items=[
                    QAItem(
                        Question("q1", "What?", "fact"),
                        Answer("raw", "raw"), Answer("csl", "csl"),
                        Score(0.2, 0.2, 0.2, 0.2, "missed temporal and relational details")
                    ),
                ],
            ),
        ],
        aggregate_compression={"mean_ratio": 10.0, "median_ratio": 10.0, "min_ratio": 10.0, "max_ratio": 10.0,
                               "total_raw_tokens": 100, "total_csl_tokens": 10, "primitive_totals": {}},
        aggregate_qa={"mean_accuracy": 0.2, "mean_completeness": 0.2, "mean_tone_match": 0.2, "mean_overall": 0.2,
                      "total_questions": 1, "successful_gradings": 1},
    )
    report = generate_report(result)
    assert "temporal" in report.lower() or "relational" in report.lower()


def test_report_includes_method_comparison():
    raw_s = Score(0.6, 0.6, 0.6, 0.6, "raw")
    csl_s = Score(0.9, 0.9, 0.9, 0.9, "csl")
    rag_s = Score(0.5, 0.5, 0.5, 0.5, "rag")
    item = QAItem(Question("q1", "?", "fact"), Answer("r", "raw"), Answer("c", "csl"),
                  csl_s, rag_answer=Answer("g", "rag"),
                  raw_score=raw_s, csl_score=csl_s, rag_score=rag_s)
    conv = ConversationResult("c1", "se", CompressionResult(1000, 100, 10.0, {"FACT": 2}), qa_items=[item])
    result = BenchmarkResult(
        conversations=[conv],
        aggregate_compression=aggregate_compression_results([conv]),
        aggregate_qa=aggregate_qa_results([conv]),
    )
    report = generate_report(result)
    assert "Method Comparison" in report
    assert "CSL beats RAG" in report


# ---------------------------------------------------------------------------
# Run all tests if executed directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---- reasoning-model param handling (#62) --------------------------------

def test_adjust_params_kimi_locks_temp_and_bumps_tokens():
    temp, mt = _adjust_params("kimi-k2.6", 0.2, 2000)
    assert temp == 1 and mt == 4000


def test_adjust_params_deepseek_bumps_tokens_keeps_temp():
    # deepseek-r1 is a reasoning model (needs headroom) but accepts temperature —
    # it must NOT be temp-locked, or determinism is silently broken.
    temp, mt = _adjust_params("deepseek-r1-distill-qwen-32b", 0.2, 2000)
    assert mt == 4000
    assert temp == 0.2


def test_adjust_params_plain_model_unchanged():
    temp, mt = _adjust_params("gpt-4o", 0.2, 2000)
    assert temp == 0.2 and mt == 2000


def test_adjust_params_never_lowers_high_max_tokens():
    temp, mt = _adjust_params("kimi-k2.6", 1, 8000)
    assert mt == 8000
