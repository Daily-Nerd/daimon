#!/usr/bin/env python3
"""State-tracking benchmark runner (ROADMAP M0.3).

For each scenario: replay every turn into each memory strategy, then probe the
final state. Grade deterministically against authored ground truth. Aggregate
overall accuracy, the override-probe subset (the discriminating cut), and the
staleness rate (how often a strategy asserts a now-wrong value on overrides).

    python benchmark/state/run_state_benchmark.py \
        --output benchmark/results/state/ \
        --update-model claude-haiku-4-5-via-meridian \
        --answer-model claude-haiku-4-5-via-meridian \
        --budget 300
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.evaluate import LLMClient, count_tokens, logger
from benchmark.state.scenarios import all_scenarios, Scenario
from benchmark.state.memories import build_memories
from benchmark.state.grade import answer_state, grade_state


def run_scenario(scenario: Scenario, llm: LLMClient, update_model: str,
                 answer_model: str, budget: int, with_graphiti: bool = False) -> Dict:
    memories = build_memories(llm, model=update_model, budget=budget)
    graphiti_mem = None
    if with_graphiti:
        # Lazy import — only when explicitly enabled (needs graphiti-core,
        # fastembed, and a reachable FalkorDB).
        from benchmark.state.graphiti_memory import GraphitiMemory
        graphiti_mem = GraphitiMemory(group_id=scenario.id)
        memories.append(graphiti_mem)
    logger.info("[%s] replaying %d turns into %d memories",
                scenario.id, len(scenario.turns), len(memories))

    # Replay the whole conversation into each memory.
    for turn in scenario.turns:
        for mem in memories:
            mem.observe(turn)

    # Probe final state.
    per_method: Dict[str, Dict] = {}
    for mem in memories:
        results = []
        for probe in scenario.probes:
            ctx = mem.context(probe.question)
            ans = answer_state(ctx, probe.question, llm, model=answer_model)
            g = grade_state(ans, probe)
            results.append({
                "probe": probe.id, "is_override": probe.is_override,
                "gold": probe.gold, "answer": ans, **g,
            })
        per_method[mem.name] = {
            "context_tokens": mem.tokens(),
            "probes": results,
        }
        n = len(results)
        acc = sum(r["correct"] for r in results) / n if n else 0.0
        logger.info("  %-10s ctx=%d tok  acc=%.2f", mem.name, mem.tokens(), acc)

    if graphiti_mem is not None:
        graphiti_mem.close()

    return {"scenario": scenario.id, "domain": scenario.domain, "methods": per_method}


def aggregate(scenario_results: List[Dict]) -> Dict:
    methods = ["raw", "csl", "summary", "rag-append", "graphiti"]
    agg: Dict[str, Dict] = {}
    for m in methods:
        all_probes = []
        tokens = []
        for sr in scenario_results:
            md = sr["methods"].get(m)
            if not md:
                continue
            all_probes.extend(md["probes"])
            tokens.append(md["context_tokens"])
        if not all_probes:
            continue
        overrides = [p for p in all_probes if p["is_override"]]
        n, no = len(all_probes), len(overrides)
        agg[m] = {
            "overall_accuracy": sum(p["correct"] for p in all_probes) / n,
            "gold_recall": sum(p["has_gold"] for p in all_probes) / n,
            "override_accuracy": (sum(p["correct"] for p in overrides) / no) if no else None,
            "staleness_rate": (sum(p["stale"] for p in overrides) / no) if no else None,
            "mean_context_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0,
            "n_probes": n, "n_override_probes": no,
        }
    # compression ratio relative to the raw (uncapped) arm
    raw_tok = agg.get("raw", {}).get("mean_context_tokens")
    for m, a in agg.items():
        mt = a["mean_context_tokens"]
        a["compression_ratio"] = (round(raw_tok / mt, 2)
                                  if raw_tok and mt else None)
    return agg


def make_report(agg: Dict) -> str:
    L = ["# State-Tracking Benchmark Report (M0.3)", "",
         "Multi-turn conversations with overrides. Deterministic grading against",
         "authored ground truth. `csl` and `summary` consolidate via the same",
         "model + symmetric prompts at the same budget — so their gap isolates",
         "structured-CSL vs prose. `rag-append` is naive retrieval; `raw` is the",
         "uncapped ceiling. `graphiti` is the temporal-KG adoption arm (Zep engine,",
         "bi-temporal edge invalidation) — present only when run with --with-graphiti.", "",
         "| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok | Compression |",
         "|--------|-------------|--------------|-----------|-------------|----------|-------------|"]
    for m in ["raw", "csl", "summary", "rag-append", "graphiti"]:
        a = agg.get(m)
        if not a:
            continue
        ov = "n/a" if a["override_accuracy"] is None else f"{a['override_accuracy']:.3f}"
        st = "n/a" if a["staleness_rate"] is None else f"{a['staleness_rate']:.3f}"
        cr = "n/a" if a.get("compression_ratio") is None else f"{a['compression_ratio']:.2f}x"
        L.append(f"| {m} | {a['overall_accuracy']:.3f} | {ov} | {st} | "
                 f"{a['gold_recall']:.3f} | {a['mean_context_tokens']:.0f} | {cr} |")
    L.append("")
    csl, summ = agg.get("csl"), agg.get("summary")
    if csl and summ and csl["override_accuracy"] is not None and summ["override_accuracy"] is not None:
        d = csl["override_accuracy"] - summ["override_accuracy"]
        verdict = ("CSL beats prose summary" if d > 0 else
                   "Prose summary beats CSL" if d < 0 else "CSL ties prose summary")
        L.append(f"**Decisive (override accuracy, equal budget): {verdict}** "
                 f"(CSL {csl['override_accuracy']:.3f} vs summary {summ['override_accuracy']:.3f}, "
                 f"Δ={d:+.3f}).")
        L.append("")
        L.append("Override accuracy is the discriminating metric: it measures whether the")
        L.append("memory reflects the CURRENT value after a change, not a stale one.")
    graph = agg.get("graphiti")
    if graph and graph["override_accuracy"] is not None and csl and csl["override_accuracy"] is not None:
        dg = graph["override_accuracy"] - csl["override_accuracy"]
        gverdict = ("Temporal-KG (Graphiti) beats CSL" if dg > 0 else
                    "CSL beats Temporal-KG (Graphiti)" if dg < 0 else "Graphiti ties CSL")
        L.append("")
        L.append(f"**Adoption arm — {gverdict}** on override accuracy "
                 f"(Graphiti {graph['override_accuracy']:.3f} vs CSL {csl['override_accuracy']:.3f}, "
                 f"Δ={dg:+.3f}). Graphiti uses bi-temporal edge invalidation; CSL uses naive merge.")
    return "\n".join(L)


def run(output_dir: str, update_model: str, answer_model: str, budget: int,
        with_graphiti: bool = False) -> Dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(cache_dir=str(out / ".llm_cache"))

    t0 = time.time()
    scenario_results = [run_scenario(s, llm, update_model, answer_model, budget,
                                     with_graphiti=with_graphiti)
                        for s in all_scenarios()]
    agg = aggregate(scenario_results)
    result = {
        "scenarios": scenario_results,
        "aggregate": agg,
        "metadata": {
            "update_model": update_model, "answer_model": answer_model,
            "budget": budget, "n_scenarios": len(scenario_results),
            "with_graphiti": with_graphiti,
            "elapsed_seconds": round(time.time() - t0, 1),
        },
    }
    with open(out / "results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(out / "report.md", "w", encoding="utf-8") as f:
        f.write(make_report(agg))
    logger.info("State benchmark complete → %s", out)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="State-tracking benchmark (M0.3)")
    p.add_argument("--output", default="benchmark/results/state/")
    p.add_argument("--update-model", default="claude-haiku-4-5-via-meridian",
                   help="Model that consolidates CSL/summary memory each turn")
    p.add_argument("--answer-model", default="claude-haiku-4-5-via-meridian",
                   help="Model that answers probes from memory")
    p.add_argument("--budget", type=int, default=300,
                   help="Equal token budget for consolidated/retrieved memory")
    p.add_argument("--with-graphiti", action="store_true",
                   help="Include the Graphiti temporal-KG arm (needs graphiti-core, "
                        "fastembed, and FalkorDB reachable at FALKORDB_HOST:FALKORDB_PORT)")
    args = p.parse_args()
    run(args.output, args.update_model, args.answer_model, args.budget,
        with_graphiti=args.with_graphiti)
    return 0


if __name__ == "__main__":
    sys.exit(main())
