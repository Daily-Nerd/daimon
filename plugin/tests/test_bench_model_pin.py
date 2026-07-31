"""#343: run-pinned served model + producer-verified checkpoint cache.

The bench's configured `model` is a gateway ALIAS (scar 0032) — it never
literally equals the wire's `response.model`, so the honest contract is not
"served == configured" but:

  - the run PINS the first observed served model (or an explicit expectation,
    BENCH_EXPECT_SERVED / --expect-served) and any question that observes a
    DIFFERENT served model fails loudly: an error row, never a silent score,
    and nothing it produced enters the cache (scar 0015: a cache replays
    whatever it was fed, so the poison gate must sit at write time);
  - every cache entry records the served model that produced it, and a read
    whose recorded producer differs from the run's pin is a MISS — verified on
    read because the served model is unknowable before the call (chicken-egg),
    so it can never live in the key.

The fakes below append to llm's served-model collector exactly where
llm._chat_litellm records the wire receipt (#458), so the pipeline under test
is the real one minus the HTTP call.
"""

import json

import pytest

from daimon_briefing import llm

from tests.bench import adapter, cache as cache_mod, metrics


class ServingChat:
    """EchoChat (test_bench_adapter) plus a wire receipt: records `served` in
    llm's #458 collector before returning, exactly as _chat_litellm does when
    the response body names the model that actually ran."""

    def __init__(self, served):
        self.served = served
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        # Simulate the response.model receipt (llm.py, #458 / scar 0032).
        llm._served_models.append(self.served)
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


def _question(qid="q_pin_1", marker="thinkpadmarker"):
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question": f"what about {marker} did we conclude",
        "answer": "x",
        "haystack_session_ids": [f"{qid}_hello", f"{qid}_gold"],
        "haystack_sessions": [_turns(f"hello{marker}"), _turns(marker)],
        "answer_session_ids": [f"{qid}_gold"],
    }


def _run_q(question, chat, cache, root):
    return adapter.run_question(
        question, chat=chat, cache=cache, backend="fake", model="alias",
        root=root, k=5, depth=20, workers=1,
    )


# ---- run-pinned served model: fail loud, never cache the foreign model ------


def test_second_question_different_served_model_fails_loudly_not_cached(tmp_path):
    cache = cache_mod.CheckpointCache(tmp_path / "c")
    # Question 1 pins the run to the first observed served model.
    result = _run_q(_question("q_a", "alphamarker"), ServingChat("model-a"),
                    cache, tmp_path / "r1")
    assert result["serialize"]["serialized"] == 2
    entries_after_q1 = sorted((tmp_path / "c").glob("*.json"))
    assert entries_after_q1

    # Question 2 observes a DIFFERENT served model: fail loudly, name both
    # models, and cache NOTHING from it.
    llm.reset_served_models()  # mirrors run.py's per-question reset
    with pytest.raises(cache_mod.ServedModelMismatch) as err:
        _run_q(_question("q_b", "betamarker"), ServingChat("model-b"),
               cache, tmp_path / "r2")
    assert "model-a" in str(err.value)
    assert "model-b" in str(err.value)
    assert sorted((tmp_path / "c").glob("*.json")) == entries_after_q1


def test_uniform_run_scores_normally(tmp_path):
    cache = cache_mod.CheckpointCache(tmp_path / "c")
    r1 = _run_q(_question("q_a", "alphamarker"), ServingChat("model-a"),
                cache, tmp_path / "r1")
    llm.reset_served_models()
    r2 = _run_q(_question("q_b", "betamarker"), ServingChat("model-a"),
                cache, tmp_path / "r2")
    assert r1["recall_at_5"] == 1.0
    assert r2["recall_at_5"] == 1.0
    assert cache.pinned_served == "model-a"


def test_expected_served_knob_rejects_first_observed_mismatch(tmp_path):
    # Explicit pin up front (#343 knob): the FIRST observed serve is checked
    # against it too — no free first observation.
    cache = cache_mod.CheckpointCache(tmp_path / "c", expected_served="model-a")
    with pytest.raises(cache_mod.ServedModelMismatch) as err:
        _run_q(_question("q_a", "alphamarker"), ServingChat("model-b"),
               cache, tmp_path / "r1")
    assert "model-a" in str(err.value)
    assert "model-b" in str(err.value)
    assert not sorted((tmp_path / "c").glob("*.json"))


def test_expected_served_knob_accepts_matching_serve(tmp_path):
    cache = cache_mod.CheckpointCache(tmp_path / "c", expected_served="model-a")
    result = _run_q(_question("q_a", "alphamarker"), ServingChat("model-a"),
                    cache, tmp_path / "r1")
    assert result["serialize"]["serialized"] == 2


# ---- cache entries record their producer; verified on READ ------------------


def test_cache_entry_records_its_producer(tmp_path):
    c = cache_mod.CheckpointCache(tmp_path)
    c.put("k", {"session_id": "s1"}, served_model="model-a")
    raw = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
    assert raw["served_model"] == "model-a"
    assert raw["checkpoint"] == {"session_id": "s1"}


def test_read_side_producer_mismatch_is_a_counted_miss(tmp_path):
    cache_mod.CheckpointCache(tmp_path).put(
        "k", {"session_id": "s1"}, served_model="model-a")
    c = cache_mod.CheckpointCache(tmp_path, expected_served="model-b")
    assert c.get("k") is None
    assert c.model_mismatch_misses == 1
    assert c.misses == 1
    assert c.hits == 0


def test_read_matching_producer_is_a_hit(tmp_path):
    cache_mod.CheckpointCache(tmp_path).put(
        "k", {"session_id": "s1"}, served_model="model-a")
    c = cache_mod.CheckpointCache(tmp_path, expected_served="model-a")
    assert c.get("k") == {"session_id": "s1"}
    assert c.hits == 1


def test_unpinned_run_adopts_the_entry_producer_as_pin(tmp_path):
    # A replayed entry's content joins this run's scores, so its recorded
    # producer is an observation too: adopting it as the pin makes a warm
    # cache with MIXED producers fail loudly instead of replaying both.
    cache_mod.CheckpointCache(tmp_path).put(
        "k", {"session_id": "s1"}, served_model="model-a")
    c = cache_mod.CheckpointCache(tmp_path)
    assert c.get("k") == {"session_id": "s1"}
    assert c.pinned_served == "model-a"


def test_legacy_entry_without_producer_field_is_a_counted_miss(tmp_path):
    # Pre-#343 entries are raw checkpoint dicts with no producer receipt —
    # exactly the shape the poisoning incident left behind, so they can never
    # be replayed (one-time cache-warm cost, accepted).
    (tmp_path / "k.json").write_text(
        json.dumps({"session_id": "s1", "working_context": {}}), encoding="utf-8")
    c = cache_mod.CheckpointCache(tmp_path)
    assert c.get("k") is None
    assert c.legacy_misses == 1
    assert c.misses == 1


def test_receiptless_entry_hits_only_a_receiptless_run(tmp_path):
    # served_model=None is honest absence (command backend, no wire receipt).
    cache_mod.CheckpointCache(tmp_path).put(
        "k", {"session_id": "s1"}, served_model=None)
    # A pinned run must not replay unattributable content.
    pinned = cache_mod.CheckpointCache(tmp_path, expected_served="model-a")
    assert pinned.get("k") is None
    assert pinned.model_mismatch_misses == 1
    # A run with no receipts on either side may.
    free = cache_mod.CheckpointCache(tmp_path)
    assert free.get("k") == {"session_id": "s1"}


# ---- aggregate carries the fail-loud outcome --------------------------------


def test_aggregate_carries_error_rows_never_silent_scores():
    scored_row = {
        "question_id": "q_ok", "abstention": False, "recall_at_5": 1.0,
        "hit_at_5": True, "mrr": 1.0, "injected_tokens": 10,
    }
    error_row = {
        "question_id": "q_bad", "error": "served_model_mismatch",
        "model_pinned": "model-a", "model_observed": ["model-b"],
    }
    agg = metrics.aggregate([scored_row, error_row], 5)
    assert agg["questions_total"] == 2
    assert agg["questions_error"] == 1
    assert agg["questions_scored"] == 1
    assert agg["questions_abstention"] == 0
    assert agg["recall_at_5"] == 1.0  # the error row never dilutes the mean


# ---- run loop: mixed run records the error row and the mixed stamp ----------


def test_run_records_error_row_and_mixed_stamp(tmp_path, monkeypatch):
    from tests.bench import run as bench_run

    questions = [_question("q_a", "alphamarker"), _question("q_b", "betamarker")]
    monkeypatch.setattr(bench_run, "_resolve_dataset",
                        lambda args: tmp_path / "ds.json")
    monkeypatch.setattr(bench_run.dataset, "load", lambda path: questions)
    monkeypatch.setattr(bench_run.dataset, "sample", lambda qs, n, seed: qs)
    monkeypatch.setattr(bench_run.dataset, "sha256_of", lambda path: "0" * 64)

    serves = iter(["model-a", "model-a", "model-b", "model-b"])

    def chat(messages, **kwargs):
        return ServingChat(next(serves))(messages, **kwargs)

    monkeypatch.setattr(bench_run.llm, "chat", chat)
    args = bench_run.build_parser().parse_args([
        "--sample", "2", "--workers", "1",
        "--cache-dir", str(tmp_path / "cache"),
        "--work-dir", str(tmp_path / "work"),
        "--out", str(tmp_path / "result.json"),
    ])
    report = bench_run.run(args)

    rows = report["per_question"]
    assert rows[0]["recall_at_5"] == 1.0
    assert rows[1]["error"] == "served_model_mismatch"
    assert rows[1]["model_pinned"] == "model-a"
    assert "model-b" in rows[1]["model_observed"]
    assert report["metrics"]["questions_error"] == 1
    # #458 stamp still sees BOTH serves: the mixed run is flagged, and the
    # foreign model's receipt is on the record even though its row errored.
    assert report["config"]["model_served"] == ["model-a", "model-b"]
    assert report["metrics"]["mixed_models"] is True
