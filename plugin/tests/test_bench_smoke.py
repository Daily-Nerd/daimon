"""LLM-dependent smoke test for the harness (#267).

Skipped unless a backend is configured AND opt-in RUN_BENCH_SMOKE=1 is set, so CI
stays green and free. Exercises the real serialize→recall path on one tiny
synthetic question against the live backend — proves the wiring, not a score.
"""

import os

import pytest

from tests.bench import adapter, cache as cache_mod, run as bench_run

_SKIP = not (os.getenv("DAIMON_LLM_API_KEY") and os.getenv("RUN_BENCH_SMOKE"))


def test_runner_parser_accepts_documented_flags():
    args = bench_run.build_parser().parse_args(
        ["--suite", "longmemeval-s", "--sample", "5", "--workers", "8"])
    assert args.sample == 5 and args.workers == 8 and args.suite == "longmemeval-s"


@pytest.mark.skipif(_SKIP, reason="set DAIMON_LLM_API_KEY and RUN_BENCH_SMOKE=1")
def test_real_backend_serialize_and_recall(tmp_path):
    from daimon_briefing import llm

    question = {
        "question_id": "smoke_1",
        "question_type": "single-session-user",
        "question": "which database did we choose for the project",
        "answer": "postgres",
        "haystack_session_ids": ["s_distractor", "s_gold"],
        "haystack_sessions": [
            [{"role": "user", "content": "let's talk about the CI pipeline setup"},
             {"role": "assistant", "content": "sure, we can use GitHub Actions"},
             {"role": "user", "content": "and caching for uv"},
             {"role": "assistant", "content": "yes, cache the uv lock"}],
            [{"role": "user", "content": "we decided to use Postgres for the database"},
             {"role": "assistant", "content": "good — Postgres it is for storage"},
             {"role": "user", "content": "with SQLAlchemy on top"},
             {"role": "assistant", "content": "agreed, SQLAlchemy ORM over Postgres"}],
        ],
        "answer_session_ids": ["s_gold"],
    }
    result = adapter.run_question(
        question, chat=llm.chat, cache=cache_mod.CheckpointCache(tmp_path / "c"),
        backend=bench_run._effective_backend(), model="live",
        root=tmp_path / "runs", k=5, depth=20, workers=2,
    )
    assert result["serialize"]["indexed"] >= 1
    assert result["n_retrieved_sessions"] >= 1


# ---- #458 / scar 0032: served-model stamp in the bench report ----
#
# The config stamp's `model` is the REQUESTED gateway alias — routing config,
# not provenance. `model_served` records what the wire said across the run;
# a run that saw more than one served model gets an explicit `mixed_models`
# flag in the aggregate block so a number is never attributed to one model
# when several actually ran. No LLM needed: pure stamp-shaping helper.


def test_stamp_served_models_single(tmp_path):
    stamp, agg = {}, {}
    bench_run._stamp_served_models(stamp, agg, ["provider/only-model"])
    assert stamp["model_served"] == "provider/only-model"
    assert "mixed_models" not in agg


def test_stamp_served_models_mixed_flags_never_silent(tmp_path):
    stamp, agg = {}, {}
    bench_run._stamp_served_models(stamp, agg, ["z-model", "a-model"])
    assert stamp["model_served"] == ["a-model", "z-model"]
    assert agg["mixed_models"] is True


def test_stamp_served_models_absent_records_nothing(tmp_path):
    # Command backend / all-cache runs have no wire receipt: honest absence,
    # never the alias copied over.
    stamp, agg = {}, {}
    bench_run._stamp_served_models(stamp, agg, [])
    assert "model_served" not in stamp
    assert "mixed_models" not in agg


def test_committed_baseline_is_annotated_and_metrics_untouched():
    # #458 acceptance: the 5-question baseline's `model` is a gateway alias
    # recorded while fallback chains were live — the config block must SAY so
    # (model_note, mirroring interim-317-baseline-first54.json), and the
    # annotation must not touch a single metric value.
    import json
    from pathlib import Path

    baseline = Path(__file__).resolve().parents[2] / \
        "benchmark/results/longmemeval-s-baseline.json"
    report = json.loads(baseline.read_text(encoding="utf-8"))
    note = report["config"]["model_note"]
    assert "alias" in note
    assert "#458" in note
    assert "unverifiable" in note
    assert report["config"]["model"] == "claude-haiku-4-5-via-meridian"
    assert report["metrics"] == {
        "k": 5,
        "questions_total": 5,
        "questions_scored": 5,
        "questions_abstention": 0,
        "recall_at_5": 0.8,
        "hit_at_5": 1.0,
        "mrr": 0.85,
        "avg_injected_tokens": 149.6,
    }
