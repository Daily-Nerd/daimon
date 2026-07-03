import json
from pathlib import Path
from dataclasses import replace
from benchmark.state.scale import extract_noise_turns, scenario_blocklist, screen_turns, build_noise_bed, chunk_windows, build_scaled_scenario
from benchmark.state.scenarios import all_scenarios
from benchmark.evaluate import count_tokens
from benchmark.state.grade import _mentions
from benchmark.state.run_state_benchmark import aggregate


def _write_jsonl(tmp_path: Path, records: list[dict]) -> str:
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(p)


def test_extract_filters_to_message_text(tmp_path):
    records = [
        {"type": "mode", "mode": "x"},                       # dropped
        {"type": "attachment", "attachment": {"a": 1}},      # dropped
        {"type": "user", "message": {"content": "hello there"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "doing the thing"},
            {"type": "tool_use", "input": {"cmd": "rm -rf"}},  # skipped
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "exit code 0"},
        ]}},
    ]
    turns = extract_noise_turns([_write_jsonl(tmp_path, records)])
    assert turns == ["hello there", "doing the thing", "exit code 0"]


def test_blocklist_collects_gold_and_stale():
    infra = next(s for s in all_scenarios() if s.id == "infra-migration")
    bl = scenario_blocklist(infra)
    assert "Rust" in bl and "Go" in bl and "Nomad" in bl and "Kubernetes" in bl


def test_screen_drops_turns_mentioning_blocked_tokens():
    bl = {"Rust", "Nomad"}
    turns = [
        "We deployed the service cleanly",      # kept
        "The team rewrote it in Rust",          # dropped (gold)
        "Switching orchestration to Nomad",     # dropped (stale)
        "Latency improved after the change",    # kept
    ]
    kept, dropped = screen_turns(turns, bl)
    assert kept == ["We deployed the service cleanly", "Latency improved after the change"]
    assert dropped == 2
    # zero-leak guarantee
    from benchmark.state.grade import _mentions
    for t in kept:
        assert not any(_mentions(t, tok) for tok in bl)


def test_build_noise_bed_reaches_target():
    turns = [f"line number {i} with some filler words here" for i in range(500)]
    bed = build_noise_bed(turns, target_tokens=200)
    assert count_tokens("\n".join(bed)) >= 200
    # did not consume everything to hit a small target
    assert len(bed) < len(turns)


def test_chunk_windows_overlap_and_count():
    turns = [f"l{i}" for i in range(250)]   # 250 lines
    windows = chunk_windows(turns, chunk_lines=100, overlap=20)
    # step = 80 -> windows start at 0,80,160,240 -> 4 windows
    assert len(windows) == 4
    first_lines = windows[0].splitlines()
    second_lines = windows[1].splitlines()
    assert len(first_lines) == 100
    # 20-line overlap: last 20 of window0 == first 20 of window1
    assert first_lines[-20:] == second_lines[:20]


def test_chunk_windows_single_when_small():
    assert len(chunk_windows(["a", "b", "c"], chunk_lines=100, overlap=20)) == 1


def test_scaled_scenario_preserves_spine_and_screens_noise():
    infra = next(s for s in all_scenarios() if s.id == "infra-migration")
    # noisy turns, some mentioning blocked tokens, plenty of clean filler
    noise = (["The quarterly review went smoothly today"] * 200
             + ["We migrated everything to Nomad"]          # must be screened out
             + ["Sprint planning covered the new dashboards"] * 200)
    scaled, meta = build_scaled_scenario(infra, noise, target_tokens=300,
                                         chunk_lines=50, overlap=10)
    # all original spine turns present, in order
    spine = infra.turns
    idxs = [scaled.turns.index(t) for t in spine]
    assert idxs == sorted(idxs)          # monotonic -> order preserved
    # probes untouched
    assert scaled.probes is infra.probes
    # zero leakage: no NON-spine turn mentions a blocked token
    bl = {tok for p in infra.probes for tok in (p.gold_terms() + p.stale)}
    spine_set = set(spine)
    for t in scaled.turns:
        if t in spine_set:
            continue
        assert not any(_mentions(t, tok) for tok in bl), t
    assert meta["dropped_noise_turns"] >= 1


def test_compression_ratio_against_raw():
    # one scenario, raw=1000 tok, summary=50 tok -> ratio 20x
    sr = [{
        "scenario": "x", "domain": "d",
        "methods": {
            "raw": {"context_tokens": 1000, "probes": [
                {"correct": True, "has_gold": True, "is_override": True, "stale": False}]},
            "summary": {"context_tokens": 50, "probes": [
                {"correct": True, "has_gold": True, "is_override": True, "stale": False}]},
        },
    }]
    agg = aggregate(sr)
    assert agg["raw"]["compression_ratio"] == 1.0
    assert abs(agg["summary"]["compression_ratio"] - 20.0) < 1e-6


def test_trend_report_flags_future_hurt():
    from benchmark.state.run_scale_benchmark import make_trend_report

    tier_aggs = {
        15000: {"summary": {"override_accuracy": 0.95, "compression_ratio": 30.0},
                "csl": {"override_accuracy": 0.95, "compression_ratio": 18.0}},
        60000: {"summary": {"override_accuracy": 0.80, "compression_ratio": 100.0},  # -15pp
                "csl": {"override_accuracy": 0.95, "compression_ratio": 60.0}},
    }
    report = make_trend_report(tier_aggs)
    assert "FUTURE-HURT" in report
    assert "0.150" in report or "-0.15" in report or "15" in report


def test_flatten_probe_rows_audit_trail():
    from benchmark.state.run_scale_benchmark import flatten_probe_rows
    scen_results = [{
        "scenario": "infra", "domain": "sw",
        "methods": {
            "summary": {"context_tokens": 280, "probes": [
                {"probe": "lang", "is_override": True, "gold": "Rust",
                 "answer": "Go and Rust", "correct": False, "has_gold": True, "stale": True},
            ]},
            "csl": {"context_tokens": 236, "probes": [
                {"probe": "lang", "is_override": True, "gold": "Rust",
                 "answer": "Rust", "correct": True, "has_gold": True, "stale": False},
            ]},
        },
    }]
    rows = flatten_probe_rows(scen_results)
    assert len(rows) == 2
    s = next(r for r in rows if r["method"] == "summary")
    assert s["scenario"] == "infra" and s["probe"] == "lang"
    assert s["answer"] == "Go and Rust" and s["correct"] is False and s["stale"] is True
    c = next(r for r in rows if r["method"] == "csl")
    assert c["correct"] is True and c["stale"] is False
