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
