#!/usr/bin/env python3
"""Tiered scale-test: run the state instrument at 2K/15K/60K over real
transcript noise, plot override-accuracy and compression-ratio vs scale.

    uv run benchmark/state/run_scale_benchmark.py \
        --tiers 2000,15000,60000 --output benchmark/results/scale/ \
        --update-model claude-haiku-4-5-via-meridian \
        --answer-model claude-haiku-4-5-via-meridian --budget 300

Optional env: set LITELLM_CONTEXT_WINDOW to the model's window (tokens) to
enable a pre-flight size check in the LLM client — an oversized prompt is then
skipped before any network call instead of stalling until the 300s timeout
(the ornith failure mode). Leave unset for known large-context models.
"""

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate import LLMClient, logger
from benchmark.state.scenarios import all_scenarios
from benchmark.state.scale import build_scaled_scenario, extract_noise_turns
from benchmark.state.run_state_benchmark import run_scenario, aggregate, make_report

TRANSCRIPT_GLOB = os.path.expanduser(
    "~/.claude/projects/-Users-kibukx-Documents-Daily-Nerd-daimon/**/*.jsonl")
PROSE_DEGRADE_PP = 0.05   # >5pp prose drop 15K->60K = future-hurt
CSL_EDGE_PP = 0.05        # >5pp CSL accuracy edge at equal budget = revisit


def discover_transcripts() -> List[str]:
    files = glob.glob(TRANSCRIPT_GLOB, recursive=True)
    return sorted(files, key=lambda p: -os.path.getsize(p))


def make_trend_report(tier_aggs: Dict[int, Dict]) -> str:
    L = ["# Scale-Test Trend Report", "",
         "| Tier | Arm | Override acc | Compression |",
         "|------|-----|--------------|-------------|"]
    for tier in sorted(tier_aggs):
        for arm in ("raw", "csl", "summary", "rag-append"):
            a = tier_aggs[tier].get(arm)
            if not a:
                continue
            ov = a.get("override_accuracy")
            cr = a.get("compression_ratio")
            ov_s = "n/a" if ov is None else f"{ov:.3f}"
            cr_s = "n/a" if cr is None else f"{cr:.2f}x"
            L.append(f"| {tier} | {arm} | {ov_s} | {cr_s} |")
    L.append("")
    # verdict: prose degradation 15K->60K and CSL edge at 60K
    tiers = sorted(tier_aggs)
    verdict = "PROSE CONFIRMED — within-noise across tiers."
    if 15000 in tier_aggs and 60000 in tier_aggs:
        s15 = tier_aggs[15000].get("summary", {}).get("override_accuracy")
        s60 = tier_aggs[60000].get("summary", {}).get("override_accuracy")
        c60 = tier_aggs[60000].get("csl", {}).get("override_accuracy")
        if s15 is not None and s60 is not None and (s15 - s60) > PROSE_DEGRADE_PP:
            verdict = (f"FUTURE-HURT — prose override-acc fell {s15 - s60:.3f} "
                       f"({s15:.3f}->{s60:.3f}) from 15K to 60K.")
        elif (c60 is not None and s60 is not None and (c60 - s60) > CSL_EDGE_PP):
            verdict = (f"REVISIT — CSL edge {c60 - s60:.3f} over prose at 60K "
                       f"equal budget.")
    L.append(f"**Verdict:** {verdict}")
    return "\n".join(L)


def flatten_probe_rows(scen_results: List[Dict]) -> List[Dict]:
    """Flatten run_scenario output to one auditable row per (scenario, method,
    probe) — the verdict's hand-verifiable trail (the model's actual answer +
    grade for every probe), so an aggregate number can be checked against the
    raw answers after the fact."""
    rows: List[Dict] = []
    for sr in scen_results:
        for method, md in sr.get("methods", {}).items():
            for p in md.get("probes", []):
                rows.append({
                    "scenario": sr.get("scenario"),
                    "method": method,
                    "probe": p.get("probe"),
                    "is_override": p.get("is_override"),
                    "gold": p.get("gold"),
                    "answer": p.get("answer"),
                    "correct": p.get("correct"),
                    "stale": p.get("stale"),
                })
    return rows


def run(tiers: List[int], output_dir: str, update_model: str, answer_model: str,
        budget: int, limit: int = 0) -> Dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(cache_dir=str(out / ".llm_cache"))
    transcripts = discover_transcripts()
    if not transcripts:
        raise SystemExit(f"no transcripts found at {TRANSCRIPT_GLOB}")
    noise = extract_noise_turns(transcripts)
    logger.info("loaded %d noise turns from %d transcripts",
                len(noise), len(transcripts))

    scenarios = all_scenarios()
    if limit:
        scenarios = scenarios[:limit]

    tier_aggs: Dict[int, Dict] = {}
    t0 = time.time()
    for tier in tiers:
        logger.info("=== tier %d tokens ===", tier)
        scen_results = []
        for s in scenarios:
            scaled, meta = build_scaled_scenario(s, noise, target_tokens=tier)
            if meta["shortfall"]:
                logger.warning("[%s] noise shortfall at tier %d (dropped %d)",
                               s.id, tier, meta["dropped_noise_turns"])
            scen_results.append(
                run_scenario(scaled, llm, update_model, answer_model, budget))
        agg = aggregate(scen_results)
        tier_aggs[tier] = agg
        (out / f"tier-{tier}").mkdir(exist_ok=True)
        (out / f"tier-{tier}" / "report.md").write_text(make_report(agg), encoding="utf-8")
        # auditable per-probe trail: the model's actual answer + grade per probe
        rows = flatten_probe_rows(scen_results)
        with open(out / f"tier-{tier}" / "answers.jsonl", "w", encoding="utf-8") as af:
            for row in rows:
                af.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = make_trend_report(tier_aggs)
    (out / "trend.md").write_text(report, encoding="utf-8")
    result = {"tier_aggregates": tier_aggs,
              "metadata": {"tiers": tiers, "update_model": update_model,
                           "answer_model": answer_model, "budget": budget,
                           "n_scenarios": len(scenarios),
                           "elapsed_seconds": round(time.time() - t0, 1)}}
    (out / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
    logger.info("scale benchmark complete -> %s", out)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Tiered memory-backend scale-test")
    p.add_argument("--tiers", default="2000,15000,60000")
    p.add_argument("--output", default="benchmark/results/scale/")
    p.add_argument("--update-model", default="claude-haiku-4-5-via-meridian")
    p.add_argument("--answer-model", default="claude-haiku-4-5-via-meridian")
    p.add_argument("--budget", type=int, default=300)
    p.add_argument("--limit", type=int, default=0, help="cap scenarios (0=all)")
    args = p.parse_args()
    tiers = [int(x) for x in args.tiers.split(",") if x.strip()]
    run(tiers, args.output, args.update_model, args.answer_model, args.budget,
        limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
