#!/usr/bin/env python3
"""Offline regrade of a finished state-benchmark run.

Re-applies the CURRENT grader (grade_state) to the answers stored in a run's
results.json — no LLM calls, no re-ingest. Use after a grader fix to see how
the verdict moves without paying for a rerun. Writes results-regraded.json and
report-regraded.md next to the originals; never overwrites them.

    python benchmark/state/regrade.py benchmark/results/state-kimi-wide/
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmark.state.scenarios import all_scenarios
from benchmark.state.grade import grade_state
from benchmark.state.run_state_benchmark import aggregate, make_report


def regrade(run_dir: str) -> dict:
    run = Path(run_dir)
    data = json.loads((run / "results.json").read_text(encoding="utf-8"))

    probes_by_key = {(s.id, p.id): p for s in all_scenarios() for p in s.probes}

    changed = 0
    for sr in data["scenarios"]:
        for method, md in sr["methods"].items():
            for rec in md["probes"]:
                probe = probes_by_key.get((sr["scenario"], rec["probe"]))
                if probe is None:  # scenario set changed since the run; skip
                    continue
                g = grade_state(rec["answer"], probe)
                if (rec["correct"], rec["has_gold"], rec["stale"]) != (
                        g["correct"], g["has_gold"], g["stale"]):
                    changed += 1
                rec.update(g)

    agg = aggregate(data["scenarios"])
    data["aggregate"] = agg
    data["regraded"] = True

    (run / "results-regraded.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (run / "report-regraded.md").write_text(make_report(agg), encoding="utf-8")
    print(f"regraded {run_dir}: {changed} probe grade(s) changed")
    return agg


if __name__ == "__main__":
    regrade(sys.argv[1] if len(sys.argv) > 1 else "benchmark/results/state-kimi-wide/")
