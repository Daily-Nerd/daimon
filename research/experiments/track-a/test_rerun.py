# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""
Tests for the Slice-2 probe-rerun harness (rerun.py) — shipped chunked
serializer re-validation + Pass 3 staleness judge. All LLM traffic is mocked;
no network.

Run:
    uv run --with pytest pytest test_rerun.py
"""

import json
import sys
from pathlib import Path

import pytest

import probe_d007 as probe
import rerun


# ---------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def clean_daimon_env(monkeypatch, tmp_path):
    """Isolate plugin config: no host env vars, no ~/.daimon/env leakage."""
    monkeypatch.setenv("DAIMON_ENV_FILE", str(tmp_path / "no-such-env-file"))
    for key in (
        "DAIMON_CHUNK_LINES", "DAIMON_CHUNK_OVERLAP", "DAIMON_CHUNK_CONCURRENCY",
        "DAIMON_MIN_MESSAGES", "DAIMON_TIMEOUT", "DAIMON_LLM_MODEL",
        "DAIMON_LLM_API_KEY", "DAIMON_LLM_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# GT texts are tuned to probe.MockLLM's word-overlap recall heuristic:
# gt1/gt2 recall against the mock reconstruction, gt3 does not.
GT_ITEMS = [
    {"id": "gt1", "type": "open_question", "trust": "verbatim",
     "text": "Verify rebuild produces a clean plan",
     "quote": "ANSWER-KEY-ONLY-QUOTE-gt1"},
    {"id": "gt2", "type": "decision", "trust": "verbatim",
     "text": "Hit rate measurement revised during the session",
     "quote": "ANSWER-KEY-ONLY-QUOTE-gt2"},
    {"id": "gt3", "type": "decision", "trust": "verbatim",
     "text": "Keep Redis eviction policy allkeys-lru",
     "quote": "ANSWER-KEY-ONLY-QUOTE-gt3"},
]


def write_session(tmp_path: Path, sid: str = "S9"):
    """Create a tmp runs/ + sessions/ pair with the synthetic transcript + GT."""
    sessions = tmp_path / "sessions"
    runs = tmp_path / "runs"
    sessions.mkdir()
    (runs / sid).mkdir(parents=True)
    (sessions / f"{sid}.txt").write_text(rerun.SYNTHETIC_TRANSCRIPT)
    (runs / sid / "ground-truth.json").write_text(
        json.dumps({"session_id": sid, "ground_truth_items": GT_ITEMS})
    )
    return runs, sessions


class RecordingLLM:
    """Tuple-interface fake. Records (system, user) per call. Staleness output
    is controlled via stale_map (id -> True/False/None); everything else
    delegates to probe.MockLLM."""

    def __init__(self, stale_map=None):
        self.calls: list[tuple[str, str]] = []
        self.stale_map = stale_map or {}

    def chat(self, messages, **kwargs):
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        self.calls.append((system, user))
        if "RECALLED ITEMS (JSON):" in user:
            start = user.index("RECALLED ITEMS (JSON):") + len("RECALLED ITEMS (JSON):")
            end = user.index("\n\nRECONSTRUCTION TEXT:")
            items = json.loads(user[start:end])
            out = [{"id": it["id"], "stale": self.stale_map.get(it["id"], False)}
                   for it in items]
            return json.dumps({"items": out}), {"total_tokens": 1}, "fake-model"
        return probe.MockLLM.chat(messages, **kwargs)

    @staticmethod
    def extract_json(text):
        return json.loads(text)

    def systems(self):
        return [s for s, _ in self.calls]

    def users(self):
        return [u for _, u in self.calls]


def assert_no_serialize_or_reconstruct(llm: RecordingLLM):
    assert rerun.serializer.SERIALIZE_SYS not in llm.systems(), "re-ran chunk serialize"
    assert rerun.serializer.MERGE_SYS not in llm.systems(), "re-ran merge"
    assert not any(u.startswith("CHECKPOINT:") for u in llm.users()), "re-ran reconstruct"


# --------------------------------------------------- transcript -> messages

def test_parse_transcript_turns():
    msgs = rerun.parse_transcript(rerun.SYNTHETIC_TRANSCRIPT)
    assert len(msgs) == 12
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert "cache TTL" in msgs[0]["content"]
    assert not msgs[0]["content"].startswith("User:")


# ------------------------------------- shipped-serializer path (threshold)

def test_serialize_single_pass_under_threshold(monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "10000")
    llm = RecordingLLM()
    cp = rerun.serialize_checkpoint("S9", rerun.SYNTHETIC_TRANSCRIPT, llm)
    assert cp["session_id"] == "S9"
    serialize_calls = [u for s, u in llm.calls if s == rerun.serializer.SERIALIZE_SYS]
    merge_calls = [u for s, u in llm.calls if s == rerun.serializer.MERGE_SYS]
    assert len(serialize_calls) == 1
    assert "chunk 1 of" not in serialize_calls[0]
    assert len(merge_calls) == 0


def test_serialize_chunked_over_threshold(monkeypatch):
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "6")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "1")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    monkeypatch.setenv("DAIMON_MERGE_GROUP_SIZE", "3")  # pin K so the fan-in is deterministic
    llm = RecordingLLM()
    cp = rerun.serialize_checkpoint("S9", rerun.SYNTHETIC_TRANSCRIPT, llm)
    assert cp["session_id"] == "S9"
    serialize_calls = [u for s, u in llm.calls if s == rerun.serializer.SERIALIZE_SYS]
    merge_calls = [u for s, u in llm.calls if s == rerun.serializer.MERGE_SYS]
    assert len(serialize_calls) >= 2, "expected chunked fan-out above threshold"
    assert "chunk 1 of" in serialize_calls[0]
    # Merge is HIERARCHICAL: partials are folded in consecutive groups of K
    # (merge_group_size) across one or more levels, so >1 partial yields one or
    # more MERGE_SYS calls — not a single pass. Pinning this to == 1 is wrong;
    # the count tracks chunk_lines × group_size, not the design intent.
    assert len(merge_calls) >= 1, "expected at least one hierarchical merge call"
    assert all("PARTIAL CHECKPOINTS" in u for u in merge_calls)


# ------------------------------------------------ score-file compatibility

def test_run_session_emits_score_py_compatible_doc(tmp_path):
    runs, sessions = write_session(tmp_path)
    res = rerun.run_session("S9", RecordingLLM(), runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "done", res.error
    score_path = runs / "S9" / "session-S9.rerun.score.json"
    assert score_path.exists()
    assert (runs / "S9" / "rerun" / "checkpoint.json").exists()
    assert (runs / "S9" / "rerun" / "reconstruction.md").exists()

    doc = json.loads(score_path.read_text())
    ss = probe.get_score_session()(doc)  # score.py must parse + compute it
    assert ss.n_gt == len(GT_ITEMS)
    assert 0.0 <= ss.rr <= 1.0
    assert 0.0 <= ss.fmr <= 1.0
    for item in doc["ground_truth_items"]:
        assert "recalled" in item
    for claim in doc["reconstruction_claims"]:
        assert "grounded" in claim


# --------------------------------------------------------- Pass 3 staleness

def test_staleness_judge_wiring(tmp_path):
    runs, sessions = write_session(tmp_path)
    # gt2 graded stale, gt1 graded null (no pinnable state) -> key omitted
    llm = RecordingLLM(stale_map={"gt1": None, "gt2": True})
    res = rerun.run_session("S9", llm, runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "done", res.error
    doc = json.loads((runs / "S9" / "session-S9.rerun.score.json").read_text())
    by_id = {i["id"]: i for i in doc["ground_truth_items"]}
    assert by_id["gt2"]["recalled"] is True
    assert by_id["gt2"]["stale"] is True
    assert "stale" not in by_id["gt1"], "null grade must omit the stale key"
    assert "stale" not in by_id["gt3"], "unrecalled item must never carry stale"
    ss = probe.get_score_session()(doc)
    assert ss.staleness == 1.0  # 1 stale of 1 graded pinnable
    assert res.staleness == 1.0


def test_judges_never_see_answer_key_in_pass2_pass3(tmp_path):
    runs, sessions = write_session(tmp_path)
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "done", res.error
    for system, user in llm.calls:
        if system in (probe.GROUNDING_JUDGE_SYS, rerun.STALENESS_JUDGE_SYS):
            assert "ANSWER-KEY-ONLY-QUOTE" not in user, \
                "GT answer-key quote leaked into a pass 2/3 judge call (.scars/0001)"


def test_staleness_prompt_contract():
    p = rerun.STALENESS_JUDGE_SYS
    assert "TRANSCRIPT" in p
    assert "doubt" in p.lower()
    assert "stale: true" in p.lower()
    assert "not evidence" in p.lower() or "not ground truth"in p.lower()


# ------------------------------------------------------------ rejudge mode

def prefill_rerun_artifacts(runs: Path, sid: str = "S9"):
    rerun_dir = runs / sid / "rerun"
    rerun_dir.mkdir(parents=True, exist_ok=True)
    cp, _, _ = probe.MockLLM.chat(
        [{"role": "user", "content": f"session_id: {sid}\n\nTRANSCRIPT:\nx"}])
    (rerun_dir / "checkpoint.json").write_text(cp)
    recon, _, _ = probe.MockLLM.chat([{"role": "user", "content": "CHECKPOINT:\n{}"}])
    (rerun_dir / "reconstruction.md").write_text(recon)
    return rerun_dir


def test_rejudge_skips_serialize_and_reconstruct(tmp_path):
    runs, sessions = write_session(tmp_path)
    prefill_rerun_artifacts(runs)
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs, sessions_dir=sessions,
                            rejudge=True, verbose=False)
    assert res.status == "rejudged", res.error
    assert_no_serialize_or_reconstruct(llm)
    assert len(llm.calls) > 0, "rejudge must still run the judge passes"
    assert (runs / "S9" / "session-S9.rerun.score.json").exists()


def test_rejudge_missing_artifacts_fails(tmp_path):
    runs, sessions = write_session(tmp_path)
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs, sessions_dir=sessions,
                            rejudge=True, verbose=False)
    assert res.status.startswith("failed:rejudge")
    assert len(llm.calls) == 0


# ------------------------------------------------------------ resumability

def test_resumability_skips_existing_score(tmp_path):
    runs, sessions = write_session(tmp_path)
    score_doc = {
        "session_id": "S9",
        "ground_truth_items": [
            {"id": "gt1", "type": "open_question", "trust": "verbatim", "recalled": True}],
        "reconstruction_claims": [],
    }
    (runs / "S9" / "session-S9.rerun.score.json").write_text(json.dumps(score_doc))
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "skipped"
    assert len(llm.calls) == 0, "skipped session must make zero LLM calls"


def test_force_overrides_skip(tmp_path):
    runs, sessions = write_session(tmp_path)
    (runs / "S9" / "session-S9.rerun.score.json").write_text(
        json.dumps({"session_id": "S9", "ground_truth_items": [],
                    "reconstruction_claims": []}))
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs, sessions_dir=sessions,
                            force=True, verbose=False)
    assert res.status == "done", res.error
    assert len(llm.calls) > 0


def test_resume_reuses_existing_serialize_artifacts(tmp_path):
    # Gateway timeouts WILL happen: when serialize+reconstruct succeeded but
    # judging died, re-invoking must only redo the missing (judge) work.
    runs, sessions = write_session(tmp_path)
    prefill_rerun_artifacts(runs)
    llm = RecordingLLM()
    res = rerun.run_session("S9", llm, runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "done", res.error
    assert_no_serialize_or_reconstruct(llm)
    assert (runs / "S9" / "session-S9.rerun.score.json").exists()


# ----------------------------------------------- originals are the baseline

def test_originals_never_overwritten(tmp_path):
    runs, sessions = write_session(tmp_path)
    originals = {
        "checkpoint.json": "ORIGINAL-CHECKPOINT-DO-NOT-TOUCH",
        "reconstruction.md": "ORIGINAL-RECONSTRUCTION-DO-NOT-TOUCH",
        "session-S9.score.json": "ORIGINAL-SCORE-DO-NOT-TOUCH",
    }
    for name, content in originals.items():
        (runs / "S9" / name).write_text(content)

    res = rerun.run_session("S9", RecordingLLM(), runs_dir=runs,
                            sessions_dir=sessions, verbose=False)
    assert res.status == "done", res.error
    for name, content in originals.items():
        assert (runs / "S9" / name).read_text() == content, f"{name} was modified"
    # rerun artifacts live in their own namespace
    assert (runs / "S9" / "rerun" / "checkpoint.json").exists()
    assert (runs / "S9" / "session-S9.rerun.score.json").exists()


# -------------------------------------------------- cycle-2 (--cycle2 mode)

CYCLE1_RECON = (
    "PART 1 — RESUMED STATE\n"
    "- Working on: cache TTL tuning for the API gateway\n"
    "- Open: verify pool size 20 under load\n\n"
    "PART 2 — DREAM SEQUENCE\n"
    "We measured 40% then revised to 71% with TTL 60s. PR #9 merges when CI "
    "is green; eviction policy stays allkeys-lru.\n"
)


def prefill_cycle1(runs: Path, sid: str = "S9") -> Path:
    """Write cycle-1 artifacts under runs/<sid>/rerun/ (cycle-2 inputs)."""
    rerun_dir = runs / sid / "rerun"
    rerun_dir.mkdir(parents=True, exist_ok=True)
    (rerun_dir / "checkpoint.json").write_text('{"session_id": "S9"}')
    (rerun_dir / "reconstruction.md").write_text(CYCLE1_RECON)
    (rerun_dir / "meta.json").write_text('{"cycle": 1}')
    return rerun_dir


def test_cycle2_wraps_cycle1_reconstruction_as_single_message(tmp_path, monkeypatch):
    runs, _ = write_session(tmp_path)
    prefill_cycle1(runs)
    seen: list[list[dict]] = []
    real = rerun.serializer.serialize_strict

    def spy(session_id, messages, **kwargs):
        seen.append(messages)
        return real(session_id, messages, **kwargs)

    monkeypatch.setattr(rerun.serializer, "serialize_strict", spy)
    llm = RecordingLLM()
    res = rerun.run_session_cycle2("S9", llm, runs_dir=runs, verbose=False)
    assert res.status == "serialized", res.error
    # the prose reconstruction must arrive as EXACTLY ONE user turn — never
    # through parse_transcript (prefix-less text must not vanish or split)
    assert len(seen) == 1
    assert len(seen[0]) == 1
    assert seen[0][0]["role"] == "user"
    assert seen[0][0]["content"] == CYCLE1_RECON
    c2 = runs / "S9" / "rerun-c2"
    assert (c2 / "checkpoint.json").exists()
    assert (c2 / "reconstruction.md").exists()
    assert (c2 / "meta.json").exists()


def test_cycle2_meta_carries_cycle_2_and_input_path(tmp_path):
    runs, _ = write_session(tmp_path)
    prefill_cycle1(runs)
    res = rerun.run_session_cycle2("S9", RecordingLLM(), runs_dir=runs, verbose=False)
    assert res.status == "serialized", res.error
    meta = json.loads((runs / "S9" / "rerun-c2" / "meta.json").read_text())
    assert meta["cycle"] == 2
    assert meta["input"].endswith("rerun/reconstruction.md")
    assert "session_id" in meta and meta["session_id"] == "S9"


def test_cycle2_missing_cycle1_artifacts_is_named_failure(tmp_path):
    # sessions/S9.txt EXISTS — cycle2 must NOT fall back to it
    runs, _ = write_session(tmp_path)
    llm = RecordingLLM()
    res = rerun.run_session_cycle2("S9", llm, runs_dir=runs, verbose=False)
    assert res.status.startswith("failed:cycle2")
    assert "cycle-1 artifacts missing" in res.status
    assert len(llm.calls) == 0, "fell back to sessions/<id>.txt or serialized anyway"
    assert not (runs / "S9" / "rerun-c2" / "checkpoint.json").exists()


def test_cycle2_resumability_skips_existing_artifacts(tmp_path):
    runs, _ = write_session(tmp_path)
    prefill_cycle1(runs)
    c2 = runs / "S9" / "rerun-c2"
    c2.mkdir(parents=True)
    (c2 / "checkpoint.json").write_text('{"session_id": "S9"}')
    (c2 / "reconstruction.md").write_text("EXISTING-C2-RECON")
    llm = RecordingLLM()
    res = rerun.run_session_cycle2("S9", llm, runs_dir=runs, verbose=False)
    assert res.status == "skipped"
    assert len(llm.calls) == 0, "skipped session must make zero LLM calls"
    assert (c2 / "reconstruction.md").read_text() == "EXISTING-C2-RECON"


def test_cycle2_force_redoes_existing_artifacts(tmp_path):
    runs, _ = write_session(tmp_path)
    prefill_cycle1(runs)
    c2 = runs / "S9" / "rerun-c2"
    c2.mkdir(parents=True)
    (c2 / "checkpoint.json").write_text('{"session_id": "S9"}')
    (c2 / "reconstruction.md").write_text("EXISTING-C2-RECON")
    llm = RecordingLLM()
    res = rerun.run_session_cycle2("S9", llm, runs_dir=runs, force=True, verbose=False)
    assert res.status == "serialized", res.error
    assert len(llm.calls) > 0
    assert (c2 / "reconstruction.md").read_text() != "EXISTING-C2-RECON"


def test_cycle2_never_touches_cycle1_dir(tmp_path):
    runs, _ = write_session(tmp_path)
    cycle1_dir = prefill_cycle1(runs)
    before = {p.name: p.read_bytes() for p in cycle1_dir.iterdir()}
    res = rerun.run_session_cycle2("S9", RecordingLLM(), runs_dir=runs, verbose=False)
    assert res.status == "serialized", res.error
    after = {p.name: p.read_bytes() for p in cycle1_dir.iterdir()}
    assert after == before, "cycle-1 artifacts are READ-ONLY inputs"


@pytest.mark.parametrize("clashing", ["--rejudge", "--serialize-only"])
def test_cycle2_mutually_exclusive_flags(monkeypatch, capsys, clashing):
    monkeypatch.setattr(sys, "argv", ["rerun.py", "--cycle2", clashing, "--all"])
    with pytest.raises(SystemExit) as excinfo:
        rerun.main()
    assert excinfo.value.code == 2  # argparse error
    assert "mutually exclusive" in capsys.readouterr().err


def test_cycle2_batch_continues_past_missing_input_and_prints_paths(
        tmp_path, monkeypatch, capsys):
    # S1 has NO cycle-1 artifacts (named failure); S2 has them (must run)
    sessions = tmp_path / "sessions"
    runs = tmp_path / "runs"
    sessions.mkdir()
    for sid in ("S1", "S2"):
        (runs / sid).mkdir(parents=True)
        (sessions / f"{sid}.txt").write_text(rerun.SYNTHETIC_TRANSCRIPT)
        (runs / sid / "ground-truth.json").write_text(
            json.dumps({"session_id": sid, "ground_truth_items": GT_ITEMS}))
    prefill_cycle1(runs, "S2")
    monkeypatch.setattr(rerun, "RUNS_DIR", runs)
    monkeypatch.setattr(rerun, "SESSIONS_DIR", sessions)
    # GatewayLLM delegates to plugin_llm.chat — patch the network boundary so
    # no real HTTP calls are made (PluginGatewayLLM is no longer used by main()).
    _mock_llm = RecordingLLM()
    def _fake_plugin_chat(messages, **kwargs):
        content, _usage, _model = _mock_llm.chat(messages, **kwargs)
        return content
    monkeypatch.setattr(rerun.plugin_llm, "chat", _fake_plugin_chat)
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "mock-model")
    monkeypatch.setattr(sys, "argv", ["rerun.py", "--cycle2", "--all"])
    rc = rerun.main()
    assert rc == 1  # S1 failed -> nonzero, but S2 still ran
    assert (runs / "S2" / "rerun-c2" / "checkpoint.json").exists()
    assert (runs / "S2" / "rerun-c2" / "reconstruction.md").exists()
    out = capsys.readouterr().out
    assert "cycle-1 artifacts missing" in out
    assert "rerun-c2" in out, "summary must name cycle-2 artifact paths"
    assert "external" in out.lower(), "summary must remind judging is external"


# -------------------------------------------------- judge-model split (D-split)

class SplitLLM:
    """Distinct fake adapter — records which calls it receives.

    Distinguishes generation calls (serialize/reconstruct) from judge calls
    (recall, grounding, staleness) by inspecting the system or user content."""

    def __init__(self, name: str, stale_map=None):
        self.name = name
        self.calls: list[tuple[str, str]] = []
        self.stale_map = stale_map or {}

    def chat(self, messages, **kwargs):
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        self.calls.append((system, user))
        # staleness branch
        if "RECALLED ITEMS (JSON):" in user:
            start = user.index("RECALLED ITEMS (JSON):") + len("RECALLED ITEMS (JSON):")
            end = user.index("\n\nRECONSTRUCTION TEXT:")
            items = json.loads(user[start:end])
            out = [{"id": it["id"], "stale": self.stale_map.get(it["id"], False)}
                   for it in items]
            return json.dumps({"items": out}), {"total_tokens": 1}, f"{self.name}-model"
        return probe.MockLLM.chat(messages, **kwargs)

    @staticmethod
    def extract_json(text):
        return json.loads(text)

    def systems(self):
        return [s for s, _ in self.calls]

    def users(self):
        return [u for _, u in self.calls]


def _is_judge_call(system: str, user: str) -> bool:
    """True if this call is a judge (recall, grounding, staleness) not generation."""
    return (
        system in (probe.RECALL_JUDGE_SYS, probe.GROUNDING_JUDGE_SYS,
                   rerun.STALENESS_JUDGE_SYS)
        or "RECALLED ITEMS (JSON):" in user
    )


def _is_gen_call(system: str, user: str) -> bool:
    """True if this call is serialization or reconstruction."""
    return (
        system in (rerun.serializer.SERIALIZE_SYS, rerun.serializer.MERGE_SYS)
        or user.startswith("CHECKPOINT:")
    )


def test_judge_split_routes_judge_calls_to_judge_llm(tmp_path):
    """When judge_llm is provided, ALL judge calls (recall, grounding, staleness)
    go to judge_llm and ZERO judge calls go to gen_llm. Generation calls
    (serialize, reconstruct) stay on gen_llm."""
    runs, sessions = write_session(tmp_path)
    gen_llm = SplitLLM("gen")
    judge_llm = SplitLLM("judge")

    res = rerun.run_session(
        "S9", gen_llm, judge_llm=judge_llm,
        runs_dir=runs, sessions_dir=sessions, verbose=False,
    )
    assert res.status == "done", res.error

    # Judge mock must have received judge calls
    judge_calls = [(s, u) for s, u in judge_llm.calls if _is_judge_call(s, u)]
    assert len(judge_calls) > 0, "judge_llm received no judge calls"

    # Gen mock must NOT have received any judge calls
    gen_judge_calls = [(s, u) for s, u in gen_llm.calls if _is_judge_call(s, u)]
    assert len(gen_judge_calls) == 0, (
        f"gen_llm received judge calls (should be zero): {gen_judge_calls[:2]}"
    )

    # Gen mock must have received at least generation calls
    gen_gen_calls = [(s, u) for s, u in gen_llm.calls if _is_gen_call(s, u)]
    assert len(gen_gen_calls) > 0, "gen_llm received no generation calls"

    # Judge mock must NOT have received generation calls
    judge_gen_calls = [(s, u) for s, u in judge_llm.calls if _is_gen_call(s, u)]
    assert len(judge_gen_calls) == 0, (
        f"judge_llm received generation calls (should be zero): {judge_gen_calls[:2]}"
    )


def test_judge_split_back_compat_single_llm(tmp_path):
    """When judge_llm is NOT provided, a single llm_mod receives ALL calls —
    exact same behavior as before the split."""
    runs, sessions = write_session(tmp_path)
    single_llm = SplitLLM("single")

    res = rerun.run_session(
        "S9", single_llm,
        runs_dir=runs, sessions_dir=sessions, verbose=False,
    )
    assert res.status == "done", res.error

    judge_calls = [(s, u) for s, u in single_llm.calls if _is_judge_call(s, u)]
    gen_calls = [(s, u) for s, u in single_llm.calls if _is_gen_call(s, u)]
    assert len(judge_calls) > 0, "single_llm must receive judge calls"
    assert len(gen_calls) > 0, "single_llm must receive generation calls"


def test_judge_split_meta_records_judge_model(tmp_path):
    """meta.json must carry a 'judge_model' field when a split judge is used."""
    runs, sessions = write_session(tmp_path)
    gen_llm = SplitLLM("gen")
    judge_llm = SplitLLM("judge")

    res = rerun.run_session(
        "S9", gen_llm, judge_llm=judge_llm,
        runs_dir=runs, sessions_dir=sessions, verbose=False,
    )
    assert res.status == "done", res.error

    meta = json.loads((runs / "S9" / "rerun" / "meta.json").read_text())
    assert "judge_model" in meta, "meta.json must have a judge_model field"


def test_gatewayllm_instance_injects_model():
    """GatewayLLM(model=X).chat passes model=X down to plugin_llm.chat."""
    import os
    captured: list[dict] = []

    def fake_chat(messages, **kwargs):
        captured.append(kwargs)
        return "ok"

    import rerun as rr
    original = rr.plugin_llm.chat
    try:
        rr.plugin_llm.chat = fake_chat
        os.environ["DAIMON_LLM_MODEL"] = "config-default"
        g = rr.GatewayLLM(model="explicit-override")
        g.chat([{"role": "user", "content": "hi"}])
        assert captured[0].get("model") == "explicit-override", (
            f"GatewayLLM did not inject model kwarg: {captured}"
        )
    finally:
        rr.plugin_llm.chat = original
        os.environ.pop("DAIMON_LLM_MODEL", None)


def test_gatewayllm_none_model_uses_config_default(monkeypatch):
    """GatewayLLM(model=None).chat does NOT inject model= — lets plugin use config."""
    captured: list[dict] = []

    def fake_chat(messages, **kwargs):
        captured.append(kwargs)
        return "ok"

    monkeypatch.setattr(rerun.plugin_llm, "chat", fake_chat)
    monkeypatch.setenv("DAIMON_LLM_MODEL", "config-model")
    g = rerun.GatewayLLM(model=None)
    g.chat([{"role": "user", "content": "hi"}])
    assert "model" not in captured[0], (
        "GatewayLLM(None) must not inject model= kwarg"
    )
