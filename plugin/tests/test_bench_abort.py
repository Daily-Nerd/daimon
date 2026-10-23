"""#344: a run whose serializes (nearly) all failed aborts instead of scoring.

The per-question adapter converts every per-call failure into a `failed`
tally and keeps going (by design: one bad session must not kill a 500
question run). The runner is the layer that sees the whole run, so the
runner owns the sanity gate: when `failed / (failed + serialized + cached)`
(the share of the sessions that reached the serializer which never made it
into the sample; too-short sessions and #343 error rows are not in it) exceeds
SERIALIZE_FAILURE_RATIO_MAX the run exits nonzero and writes NO results file.
The existence of a results file implies the pipeline ran. `--force-score`
writes the report anyway and marks it forced, so a forced report can never be
mistaken for a clean one.

No LLM: the fakes below are the chat seam the adapter already takes. The
all-fail fake raises exactly what llm.py raises on an HTTP 401 (the incident
that motivated the issue).
"""

import json

import pytest

from daimon_briefing import llm

from tests.bench import run as bench_run


class Unauthorized:
    """The incident backend: every call is an HTTP 401 (llm.py raises ChatError
    without a transport retry for a 4xx)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        raise llm.ChatError("LLM HTTP 401 (response body suppressed)")


class FixedChat:
    """A healthy backend: a fixed, prompt-collision-proof checkpoint (scar
    0030: never key a test on a marker parsed out of the prompt blob)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        return json.dumps({
            "session_id": "ignored",
            "working_context": {
                "active_topic": {"text": "session about zebra quartz lantern",
                                 "trust": "inferred"},
                "open_questions": [],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [], "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        })


class FailFirst:
    """Healthy backend whose FIRST call fails: a partial failure ratio."""

    def __init__(self):
        self.calls = 0
        self._ok = FixedChat()

    def __call__(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise llm.ChatError("LLM HTTP 401 (response body suppressed)")
        return self._ok(messages, **kwargs)


def _turns(word):
    return [
        {"role": "user", "content": f"let us discuss {word} today please"},
        {"role": "assistant", "content": f"sure, {word} is interesting"},
        {"role": "user", "content": f"tell me more about {word}"},
        {"role": "assistant", "content": f"here is more on {word}"},
    ]


def _question(qid):
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question": "what did we conclude about zebra quartz lantern",
        "answer": "x",
        "haystack_session_ids": [f"{qid}_a", f"{qid}_b"],
        # Content is per question: the checkpoint cache keys on transcript
        # content, so identical sessions across questions would be cache hits
        # (never a call on the fake) and the failure arithmetic below would
        # change under the fixture.
        "haystack_sessions": [_turns(f"alpha {qid}"), _turns(f"beta {qid}")],
        "answer_session_ids": [f"{qid}_b"],
    }


def _wire(monkeypatch, tmp_path, chat, n_questions=1):
    questions = [_question(f"q_{i}") for i in range(n_questions)]
    monkeypatch.setattr(bench_run, "_resolve_dataset",
                        lambda args: tmp_path / "ds.json")
    monkeypatch.setattr(bench_run.dataset, "load", lambda path: questions)
    monkeypatch.setattr(bench_run.dataset, "sample", lambda qs, n, seed: qs)
    monkeypatch.setattr(bench_run.dataset, "sha256_of", lambda path: "0" * 64)
    monkeypatch.setattr(bench_run.llm, "chat", chat)
    out = tmp_path / "result.json"
    argv = [
        "--sample", str(n_questions), "--workers", "1",
        "--cache-dir", str(tmp_path / "cache"),
        "--work-dir", str(tmp_path / "work"),
        "--out", str(out),
    ]
    return argv, out


# ---- the all-fail incident: nonzero exit, no results file -----------------


def test_all_serializes_fail_aborts_and_writes_no_results_file(
        tmp_path, monkeypatch):
    chat = Unauthorized()
    argv, out = _wire(monkeypatch, tmp_path, chat)
    args = bench_run.build_parser().parse_args(argv)
    with pytest.raises(bench_run.SerializeFailuresExceeded) as err:
        bench_run.run(args)
    assert chat.calls == 2
    assert not out.exists()
    # The abort names the counts and the threshold.
    msg = str(err.value)
    assert "2 of 2" in msg
    assert "1.00" in msg
    assert f"{bench_run.SERIALIZE_FAILURE_RATIO_MAX:.2f}" in msg


def test_main_exits_nonzero_on_abort_and_says_why(tmp_path, monkeypatch, capsys):
    argv, out = _wire(monkeypatch, tmp_path, Unauthorized())
    rc = bench_run.main(argv)
    assert rc == bench_run.EXIT_SERIALIZE_FAILURES
    assert rc != 0
    assert not out.exists()
    captured = capsys.readouterr()
    assert "2 of 2" in captured.err
    assert "--force-score" in captured.err


def test_main_refuses_a_results_file_when_every_serialize_fails(
        tmp_path, monkeypatch):
    # The contract in one assertion: a results file implies the pipeline ran.
    # Uses only names that predate #344 (main, build_parser), so on a tree
    # without the gate this fails on the assertions, never on an import.
    argv, out = _wire(monkeypatch, tmp_path, Unauthorized())
    rc = bench_run.main(argv)
    assert rc != 0
    assert not out.exists()


def test_partial_failure_past_threshold_also_aborts(tmp_path, monkeypatch):
    # 2 questions x 2 sessions = 4 attempts, 1 failure: 0.25 > the threshold.
    # The gate is a RATIO over the sessions that reached the serializer, not
    # an all-or-nothing.
    argv, out = _wire(monkeypatch, tmp_path, FailFirst(), n_questions=2)
    args = bench_run.build_parser().parse_args(argv)
    with pytest.raises(bench_run.SerializeFailuresExceeded) as err:
        bench_run.run(args)
    assert not out.exists()
    assert "1 of 4" in str(err.value)


# ---- --force-score: the report is written AND marked ----------------------


def test_force_score_writes_a_report_that_says_it_was_forced(
        tmp_path, monkeypatch):
    argv, out = _wire(monkeypatch, tmp_path, Unauthorized())
    rc = bench_run.main(argv + ["--force-score"])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["metrics"]["forced_score"] is True
    assert report["metrics"]["serialize_failed"] == 2
    assert report["metrics"]["serialize_sessions"] == 2
    assert report["metrics"]["serialize_failure_ratio"] == 1.0
    # The failure tallies ride along per question, untouched.
    assert report["per_question"][0]["serialize"]["failed"] == 2
    assert report["per_question"][0]["serialize"]["serialized"] == 0


def test_force_score_on_a_clean_run_leaves_no_forced_marker(
        tmp_path, monkeypatch):
    # The marker records that the gate FIRED and was overridden, not that the
    # flag was typed: a clean run under --force-score is still a clean report.
    argv, out = _wire(monkeypatch, tmp_path, FixedChat())
    rc = bench_run.main(argv + ["--force-score"])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "forced_score" not in report["metrics"]


# ---- a clean run records its (zero) failure ratio and no marker ------------


def test_clean_run_records_zero_ratio_and_no_forced_marker(
        tmp_path, monkeypatch):
    argv, out = _wire(monkeypatch, tmp_path, FixedChat())
    report = bench_run.run(bench_run.build_parser().parse_args(argv))
    assert out.exists()
    assert "forced_score" not in report["metrics"]
    assert report["metrics"]["serialize_failed"] == 0
    assert report["metrics"]["serialize_sessions"] == 2
    assert report["metrics"]["serialize_failure_ratio"] == 0.0


# ---- a warm cache is part of the sample: it sits in the denominator --------


def test_warm_rerun_with_one_fresh_failure_scores_and_records_it(
        tmp_path, monkeypatch):
    # Run 1 warms the cache with 5 questions (10 sessions). Run 2 adds a sixth
    # question whose first session fails: 1 failed, 1 serialized, 10 cached.
    # The gate measures how much of the scored SAMPLE is missing, and a cached
    # session is present in the sample, so the ratio is 1/12, under the line:
    # the run scores and the report says 1 failed.
    argv, out = _wire(monkeypatch, tmp_path, FixedChat(), n_questions=5)
    assert bench_run.main(argv) == 0
    argv, out = _wire(monkeypatch, tmp_path, FailFirst(), n_questions=6)
    assert bench_run.main(argv) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    m = report["metrics"]
    assert m["serialize_failed"] == 1
    assert m["serialize_sessions"] == 12
    assert m["serialize_failure_ratio"] == pytest.approx(1 / 12)
    assert "forced_score" not in m
    tallies = [q["serialize"] for q in report["per_question"]]
    assert sum(t["cached"] for t in tallies) == 10
    assert sum(t["serialized"] for t in tallies) == 1


# ---- the gate itself: pure over per-question rows --------------------------


def _row(failed, serialized, cached=0, too_short=0):
    return {"serialize": {"failed": failed, "serialized": serialized,
                          "cached": cached, "too_short": too_short,
                          "indexed": serialized + cached}}


def test_gate_denominator_is_every_session_that_reached_the_serializer():
    # failed + serialized + cached: a cached session is in the sample. Too
    # short sessions never reached the serializer and are not in it. #343
    # error rows carry no serialize block at all: skipped, not crashed.
    rows = [_row(1, 3, cached=6, too_short=5),
            {"question_id": "q_err", "error": "served_model_mismatch"}]
    gate = bench_run.serialize_failure_gate(rows)
    assert gate.failed == 1
    assert gate.sessions == 10
    assert gate.ratio == 0.1
    assert gate.exceeded is False


def test_gate_warm_rerun_one_failure_in_three_fresh_calls_passes():
    # 190 cached, 2 serialized, 1 failed: the one bad call is 1/193 of the
    # sample, not 1/3 of the fresh calls. The report still records it.
    gate = bench_run.serialize_failure_gate([_row(1, 2, cached=190)])
    assert gate.failed == 1
    assert gate.sessions == 193
    assert gate.exceeded is False


def test_gate_with_no_sessions_has_no_ratio_and_never_trips():
    # Every session was too short: nothing reached the serializer. The ratio
    # is honest absence (None), never 0.0 dressed up as a clean pass.
    gate = bench_run.serialize_failure_gate([_row(0, 0, too_short=4)])
    assert gate.sessions == 0
    assert gate.ratio is None
    assert gate.exceeded is False


def test_gate_trips_strictly_above_the_threshold():
    # "exceeds" means strictly greater: sitting exactly on the line passes.
    thr = bench_run.SERIALIZE_FAILURE_RATIO_MAX
    assert 0.0 < thr < 1.0
    n = 100
    on_the_line = bench_run.serialize_failure_gate(
        [_row(int(round(thr * n)), n - int(round(thr * n)))])
    assert on_the_line.ratio == pytest.approx(thr)
    assert on_the_line.exceeded is False
    above = bench_run.serialize_failure_gate(
        [_row(int(round(thr * n)) + 1, n - int(round(thr * n)) - 1)])
    assert above.exceeded is True
    all_fail = bench_run.serialize_failure_gate([_row(7, 0)])
    assert all_fail.ratio == 1.0
    assert all_fail.exceeded is True


def test_parser_accepts_force_score_and_defaults_off():
    assert bench_run.build_parser().parse_args([]).force_score is False
    assert bench_run.build_parser().parse_args(["--force-score"]).force_score
