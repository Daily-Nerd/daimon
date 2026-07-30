"""End-to-end adapter test (#267): serialize → store → recall, deterministic LLM.

Uses an echo fake for `chat` that reflects a distinctive marker token from each
session's transcript into its checkpoint, so recall genuinely discriminates
sessions by content — the real FTS path, no LLM.
"""

import json

from tests.bench import adapter, cache as cache_mod


class EchoChat:
    """Fake serializer backend: emits a valid checkpoint whose active_topic echoes
    the `*marker` token found in the session transcript it is handed."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        blob = " ".join(str(m.get("content") or "") for m in messages)
        marker = next((w for w in blob.split() if w.endswith("marker")), "nomarker")
        return json.dumps({
            "session_id": "ignored",
            "working_context": {
                "active_topic": {"text": f"session about {marker}", "trust": "inferred"},
                "open_questions": [],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
            },
            "worker_queue": [],
        })


def _turns(marker):
    return [
        {"role": "user", "content": f"let us discuss {marker} today please"},
        {"role": "assistant", "content": f"sure, {marker} is interesting"},
        {"role": "user", "content": f"tell me more about {marker}"},
        {"role": "assistant", "content": f"here is more on {marker}"},
    ]


def _question():
    return {
        "question_id": "q_bench_1",
        "question_type": "single-session-user",
        "question": "what about thinkpadmarker did we conclude",
        "answer": "x",
        "haystack_session_ids": ["sess_hello", "sess_gold"],
        "haystack_sessions": [_turns("hellomarker"), _turns("thinkpadmarker")],
        "answer_session_ids": ["sess_gold"],
    }


def test_run_question_retrieves_the_gold_session(tmp_path):
    result = adapter.run_question(
        _question(), chat=EchoChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    assert result["serialize"]["indexed"] == 2
    assert result["serialize"]["serialized"] == 2
    assert result["n_retrieved_sessions"] >= 1
    # the gold session's checkpoint carries the query's marker; the other does not
    assert result["hit_at_5"] is True
    assert result["recall_at_5"] == 1.0
    assert result["mrr"] == 1.0
    assert result["injected_tokens"] > 0


def test_cache_hit_skips_the_llm_on_second_run(tmp_path):
    cache = cache_mod.CheckpointCache(tmp_path / "c")
    chat = EchoChat()
    q = _question()
    adapter.run_question(q, chat=chat, cache=cache, backend="fake", model="fake",
                         root=tmp_path / "r1", k=5, depth=20, workers=1)
    first = chat.calls
    assert first == 2
    # second run, same content+config -> all cache hits, zero new LLM calls
    adapter.run_question(q, chat=chat, cache=cache, backend="fake", model="fake",
                         root=tmp_path / "r2", k=5, depth=20, workers=1)
    assert chat.calls == first  # no additional serialize calls


def test_abstention_question_scores_none(tmp_path):
    q = _question()
    q["question_id"] = "q_bench_abs"
    q["answer_session_ids"] = []
    result = adapter.run_question(
        q, chat=EchoChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    assert result["abstention"] is True
    assert result["recall_at_5"] is None
    assert result["mrr"] is None


def test_env_is_restored_after_a_question(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CARRY", "sentinel")
    adapter.run_question(
        _question(), chat=EchoChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    import os
    assert os.environ["DAIMON_CARRY"] == "sentinel"


# ---- #405: forbidden-hit scoring, wired through the real pipeline -----------


class SecretChat:
    """Fake serializer backend that always emits a checkpoint whose active_topic
    carries a distinctive secret token plus a query-matching term — so the token
    reliably reaches the assembled brief regardless of the transcript."""

    def __call__(self, messages, **kwargs):
        return json.dumps({
            "session_id": "ignored",
            "working_context": {
                "active_topic": {
                    "text": "database choice: secret_leak_token was postgres",
                    "trust": "inferred"},
                "open_questions": [], "recent_decisions": [],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
            },
            "worker_queue": [],
        })


def _leak_question():
    q = _question()
    q["question"] = "which database choice did we make"
    return q


def test_run_question_scores_forbidden_leak_against_the_brief(tmp_path):
    # SecretChat leaks "secret_leak_token" into every checkpoint's topic, so the
    # assembled brief carries it — forbidding it must count as a leak that FLOORS
    # the case (base recall 1.0, one of one forbidden hit → penalized 0.0).
    q = _leak_question()
    q["forbidden_hits"] = ["secret_leak_token"]
    result = adapter.run_question(
        q, chat=SecretChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    assert result["recall_at_5"] == 1.0
    assert result["forbidden_total"] == 1
    assert result["forbidden_matched"] == 1
    assert result["forbidden_hit"] is True
    assert result["recall_at_5_penalized"] == 0.0


def test_run_question_clean_when_forbidden_material_absent(tmp_path):
    q = _question()
    q["forbidden_hits"] = ["nonexistent secret phrase xyzzy"]
    result = adapter.run_question(
        q, chat=EchoChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    assert result["forbidden_total"] == 1
    assert result["forbidden_matched"] == 0
    assert result["forbidden_hit"] is False
    # a clean case keeps its base recall untouched
    assert result["recall_at_5_penalized"] == result["recall_at_5"]


def test_run_question_without_forbidden_field_reports_none(tmp_path):
    result = adapter.run_question(
        _question(), chat=EchoChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend="fake", model="fake", root=tmp_path / "runs", k=5, depth=20, workers=1,
    )
    assert result["forbidden_total"] == 0
    assert result["forbidden_hit"] is None
    assert result["recall_at_5_penalized"] == result["recall_at_5"]
