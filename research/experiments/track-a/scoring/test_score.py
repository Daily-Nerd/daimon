# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""
Tests for the Track A scorer — staleness-rate metric (Q-STALE) plus
backward-compatibility guarantees for legacy score files.

Run:
    uv run --with pytest pytest test_score.py
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import score


# ---------------------------------------------------------------- helpers

def gt(id_, recalled, trust="verbatim", stale=None):
    item = {"id": id_, "type": "decision", "trust": trust, "recalled": recalled}
    if stale is not None:
        item["stale"] = stale
    return item


def claim(id_, grounded):
    return {"id": id_, "text": f"claim {id_}", "grounded": grounded}


def doc(sid="S1", gt_items=None, claims=None):
    return {
        "session_id": sid,
        "ground_truth_items": gt_items or [],
        "reconstruction_claims": claims or [],
    }


LEGACY_DOC = doc(
    sid="LEG",
    gt_items=[gt("gt1", True), gt("gt2", True), gt("gt3", False), gt("gt4", True)],
    claims=[claim("r1", True), claim("r2", True), claim("r3", False)],
)


# -------------------------------------------------- staleness computation

class TestStalenessRate:
    def test_zero_stale(self):
        s = score.score_session(doc(gt_items=[
            gt("gt1", True, stale=False),
            gt("gt2", True, stale=False),
            gt("gt3", True),
        ]))
        assert s.staleness == 0.0
        assert s.n_pinnable == 2
        assert s.n_stale == 0

    def test_some_stale(self):
        s = score.score_session(doc(gt_items=[
            gt("gt1", True, stale=True),
            gt("gt2", True, stale=False),
            gt("gt3", True, stale=False),
            gt("gt4", True, stale=False),
        ]))
        assert s.staleness == pytest.approx(0.25)
        assert s.n_pinnable == 4
        assert s.n_stale == 1

    def test_all_stale(self):
        s = score.score_session(doc(gt_items=[
            gt("gt1", True, stale=True),
            gt("gt2", True, stale=True),
        ]))
        assert s.staleness == 1.0

    def test_no_pinnable_items_is_no_data(self):
        s = score.score_session(doc(gt_items=[gt("gt1", True), gt("gt2", False)]))
        assert s.staleness is None
        assert s.n_pinnable == 0
        assert s.n_stale == 0

    def test_unrecalled_items_never_count_as_pinnable(self):
        # `stale` on a non-recalled item is grader noise — denominator is
        # *recalled* items with pinnable state.
        s = score.score_session(doc(gt_items=[
            gt("gt1", False, stale=True),
            gt("gt2", True, stale=False),
        ]))
        assert s.staleness == 0.0
        assert s.n_pinnable == 1

    def test_stale_items_still_count_as_recalled_for_rr(self):
        # Staleness does NOT change RR semantics — historical numbers stay
        # comparable. A stale item IS recalled, just pinned to the wrong state.
        s = score.score_session(doc(gt_items=[
            gt("gt1", True, stale=True),
            gt("gt2", True, stale=True),
            gt("gt3", False),
            gt("gt4", True),
        ]))
        assert s.rr == pytest.approx(0.75)
        assert s.staleness == 1.0


# ------------------------------------------------- backward compatibility

class TestLegacyScoreFiles:
    def test_legacy_doc_scores_identically(self):
        s = score.score_session(LEGACY_DOC)
        assert s.rr == pytest.approx(0.75)
        assert s.fmr == pytest.approx(1 / 3)
        assert s.omr == pytest.approx(0.25)
        assert s.n_gt == 4
        assert s.n_claims == 3
        assert s.n_false == 1

    def test_legacy_doc_reports_no_staleness_data(self):
        s = score.score_session(LEGACY_DOC)
        assert s.staleness is None


# ------------------------------------------------------------- aggregate

class TestStalenessAggregate:
    def test_mean_over_sessions_with_data_only(self):
        scores = [
            score.score_session(doc(sid="A", gt_items=[gt("g1", True, stale=True)])),
            score.score_session(doc(sid="B", gt_items=[
                gt("g1", True, stale=False), gt("g2", True, stale=False)])),
            score.score_session(LEGACY_DOC),  # no data — excluded from mean
        ]
        lines = score.staleness_lines(scores)
        joined = "\n".join(lines)
        assert "50.0%" in joined          # mean of 100% and 0%
        assert "2 of 3" in joined         # sessions with data
        assert "ADVISORY" in joined

    def test_no_data_anywhere(self):
        lines = score.staleness_lines([score.score_session(LEGACY_DOC)])
        joined = "\n".join(lines)
        assert "no data" in joined.lower()

    def test_advisory_pass_at_or_under_bar(self):
        # 1 stale of 10 pinnable = 10% — exactly at the bar, passes.
        items = [gt(f"g{i}", True, stale=(i == 0)) for i in range(10)]
        lines = score.staleness_lines([score.score_session(doc(gt_items=items))])
        assert "PASS" in "\n".join(lines)

    def test_advisory_fail_over_bar(self):
        items = [gt(f"g{i}", True, stale=(i < 2)) for i in range(10)]
        lines = score.staleness_lines([score.score_session(doc(gt_items=items))])
        assert "FAIL" in "\n".join(lines)

    def test_staleness_never_changes_verdict(self):
        # PASS-worthy RR/FMR with 100% staleness must still be PASS — the
        # staleness bar is advisory, gates stay RR/FMR.
        items = [gt(f"g{i}", True, stale=True) for i in range(10)]
        claims = [claim(f"r{i}", True) for i in range(10)]
        s = score.score_session(doc(gt_items=items, claims=claims))
        v, _ = score.verdict([s])
        assert v == "PASS — BUILD"


# ------------------------------------------------------------- CLI output

def run_cli(tmp_path: Path, docs: list[dict]) -> str:
    paths = []
    for d in docs:
        p = tmp_path / f"session-{d['session_id']}.score.json"
        p.write_text(json.dumps(d))
        paths.append(str(p))
    script = Path(__file__).with_name("score.py")
    out = subprocess.run(
        [sys.executable, str(script), *paths],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


class TestVerdictRendering:
    def test_staleness_column_and_advisory_line(self, tmp_path):
        d = doc(sid="S9", gt_items=[
            gt("g1", True, stale=True),
            gt("g2", True, stale=False),
        ], claims=[claim("r1", True)])
        out = run_cli(tmp_path, [d])
        assert "stale" in out.lower()
        assert "50.0%" in out
        assert "ADVISORY" in out

    def test_legacy_file_renders_no_data_and_same_verdict(self, tmp_path):
        out = run_cli(tmp_path, [LEGACY_DOC])
        assert "no data" in out.lower()
        # legacy verdict semantics untouched: RR 75% >= 70%, FMR 33% > 10%,
        # not a kill -> PIVOT
        assert "VERDICT: PIVOT" in out

    def test_advisory_does_not_gate_pass(self, tmp_path):
        items = [gt(f"g{i}", True, stale=True) for i in range(10)]
        claims = [claim(f"r{i}", True) for i in range(10)]
        out = run_cli(tmp_path, [doc(sid="SX", gt_items=items, claims=claims)])
        assert "VERDICT: PASS — BUILD" in out
        assert "FAIL" in out  # the advisory annotation itself flags it
