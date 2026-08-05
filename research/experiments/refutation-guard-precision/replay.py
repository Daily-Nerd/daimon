"""Negative-control replay of the `daimon refute guard` rails.

The guard has two rails: an exact-anchor rail (`issue:N`, which also accepts a
bare `#N` in the query) and an exact-subject rail (the record's subject as a
verbatim substring of the query, minimum eight characters). Both are advisory,
but an active refutation is permission to interrupt work, so a false fire costs
attention and, repeated, costs trust in every fire.

Design. Refutations are built from this repository's own refuted work and
replayed against checkpoint item texts drawn from OTHER projects. No unrelated
project's session can legitimately be reviving a daimon-internal approach, so
EVERY fire is a false positive by construction. There is no labelling step and
no judgement call.

Two runs:

  1. fixed    — six refutations on real anchors, to measure the rails as they
                would behave on a small authored ledger.
  2. scaling  — anchors sampled from the repository's issue-number range at
                ledger sizes 6, 20 and 60, to measure how the false-positive
                rate moves as the ledger grows.

Reads the local checkpoint store. Writes aggregates only: item text never
leaves this process, and `measurements.json` contains counts alone.

Usage:
    DAIMON_CHECKPOINT_DIR=<scratch> uv run python replay.py
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import re
import statistics
import sys
import tempfile

from daimon_briefing import refutations

SEED = 573
SEEDS = int(os.environ.get("REPLAY_SEEDS", "200"))
LEDGER_SIZES = (6, 20, 60, 100)
ISSUE_RANGE = 582  # exclusive upper bound on this repository's issue numbers

# Real refuted work from this repository's history. The last entry is a short
# generic phrase, included deliberately to probe the subject rail's floor.
FIXED = [
    ("the original 502 receipt design", "receipt tiers", "issue:502"),
    ("IDF-weighted recall term matching", "recall scoring", "issue:470"),
    ("substring matching for coverage gates", "recall gates", "issue:490"),
    ("the merge output cache", "serialize path", "issue:536"),
    ("two-model merge", "serialize path", "issue:48"),
    ("add caching", "any layer", "issue:1"),
]


def corpus(exclude: str = "Daily-Nerd-daimon") -> list[str]:
    """Item texts from every real bucket except this repository's own."""
    out: list[str] = []
    root = pathlib.Path.home() / ".daimon" / "checkpoints"
    if not root.exists():
        return out
    for bucket in sorted(root.iterdir()):
        if not bucket.is_dir() or "pytest" in bucket.name:
            continue
        if exclude and exclude in bucket.name:
            continue
        latest = bucket / "latest.json"
        if not latest.exists():
            continue
        try:
            checkpoint = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for section in ("epistemic_snapshot", "working_context", "worker_queue"):
            block = checkpoint.get(section)
            if not isinstance(block, dict):
                continue
            for value in block.values():
                if not isinstance(value, list):
                    continue
                for entry in value:
                    if not isinstance(entry, dict):
                        continue
                    text = entry.get("text") or entry.get("quote") or ""
                    if isinstance(text, str) and text.strip():
                        out.append(text.strip())
    return out


def collision_surface(texts: list[str], band: int = 100) -> dict:
    """Where the anchor rail's false positives can come from at all.

    `guard` promotes every `#N` it finds in the query to an `issue:N` anchor
    (refutations.py, `_ISSUE_RE`), so the corpus's `#N` tokens ARE the entire
    false-positive surface of that rail — an anchor can only collide with a
    number some unrelated project already writes.  Counting them by magnitude
    turns "which issues a refutation anchors on" from a hand-waved explanation
    of the seed spread into a measured property of the corpus.

    Counts only, never the surrounding text.
    """
    nums = [int(n) for text in texts
            for n in re.findall(r"(?:issue:|#)(\d+)\b", text, re.IGNORECASE)]
    in_range = [n for n in nums if 1 <= n < ISSUE_RANGE]
    bands: dict[str, int] = {}
    for n in in_range:
        low = (n - 1) // band * band + 1
        bands[f"{low}-{low + band - 1}"] = bands.get(f"{low}-{low + band - 1}", 0) + 1
    return {
        "tokens_total": len(nums),
        "tokens_in_issue_range": len(in_range),
        "distinct_in_issue_range": len(set(in_range)),
        "by_band": bands,
    }


def _seed(project: str, records) -> None:
    for subject, scope, anchor in records:
        refutations.assert_refutation(
            subject=subject, verdict="measured and rejected", scope=scope,
            evidence=["measurement:replay"], anchors=[anchor],
            authority="human", ratified=True, project_dir=project)


def _replay(project: str, texts: list[str]):
    """Replay every corpus text against whatever is seeded in `project`.

    There is deliberately no post-hoc "active subset" filter here.  The first
    version of this script seeded all 60 scaling records once and then filtered
    hits down to the anchors nominally active at each ledger size — but the
    filter passed every SUBJECT-rail hit through unconditionally, so all 60
    subject records were live in the size-6 and size-20 conditions too.  Only
    the anchor rail actually scaled.  It did not corrupt the published numbers,
    because the subject rail never fired at all, but the design could not have
    detected it if it had.  Ledger size is now varied by seeding exactly that
    many records, which is the thing the condition claims to be.
    """
    fires = {"anchor": 0, "subject": 0}
    hit_texts = 0
    per_subject: dict[str, int] = {}
    for text in texts:
        hits = refutations.guard(text[:1900], project_dir=project)
        if hits:
            hit_texts += 1
        for hit in hits:
            fires[hit["guard_match"]["rail"]] += 1
            per_subject[hit["subject"]] = per_subject.get(hit["subject"], 0) + 1
    return fires, hit_texts, per_subject


def main() -> None:
    texts = corpus()
    if not texts:
        print("no corpus found in the local checkpoint store", file=sys.stderr)
        raise SystemExit(1)

    out: dict = {"corpus_texts": len(texts), "seed": SEED,
                 "collision_surface": collision_surface(texts)}

    with tempfile.TemporaryDirectory() as fixed_project:
        _seed(fixed_project, FIXED)
        fires, hit_texts, per_subject = _replay(fixed_project, texts)
        out["fixed"] = {
            "refutations": len(FIXED),
            "fires": fires,
            "texts_hit": hit_texts,
            "rate_pct": round(100 * hit_texts / len(texts), 3),
            "by_subject": per_subject,
        }

    # One seed is one draw of "which issue numbers did this project's
    # refutations land on", and that draw dominates the result: low issue
    # numbers appear as bare `#N` in unrelated prose far more often than high
    # ones, so a seed that happens to sample #3 and #12 measures a different
    # world from one that samples #481 and #522.  The first version reported
    # seed 573 alone and read its three points as a slope.  Report the
    # distribution instead, and let the spread speak for itself.
    out["seeds"] = SEEDS
    out["scaling"] = []
    for size in LEDGER_SIZES:
        rates = []
        rail_totals = {"anchor": 0, "subject": 0}
        for seed in range(SEEDS):
            rng = random.Random(SEED + seed)
            numbers = rng.sample(range(1, ISSUE_RANGE), size)
            with tempfile.TemporaryDirectory() as scale_project:
                _seed(scale_project, [
                    (f"rejected approach number {i} in the daimon serialize path",
                     f"scope {i}", f"issue:{n}")
                    for i, n in enumerate(numbers)])
                fires, hit_texts, _ = _replay(scale_project, texts)
            rates.append(100 * hit_texts / len(texts))
            for rail in rail_totals:
                rail_totals[rail] += fires[rail]
        rates.sort()
        out["scaling"].append({
            "ledger_size": size,
            "mean_rate_pct": round(statistics.fmean(rates), 3),
            "median_rate_pct": round(statistics.median(rates), 3),
            "min_rate_pct": round(rates[0], 3),
            "max_rate_pct": round(rates[-1], 3),
            "p05_rate_pct": round(rates[int(0.05 * (len(rates) - 1))], 3),
            "p95_rate_pct": round(rates[int(0.95 * (len(rates) - 1))], 3),
            "fires_by_rail": rail_totals,
        })

    here = pathlib.Path(__file__).parent
    (here / "measurements.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"corpus (non-daimon projects): {len(texts)} item texts")
    surface = out["collision_surface"]
    print(f"anchor-rail collision surface: {surface['tokens_in_issue_range']} "
          f"in-range #N tokens, {surface['distinct_in_issue_range']} distinct")
    for band in sorted(surface["by_band"], key=lambda b: int(b.split("-")[0])):
        print(f"  {band:>9}: {surface['by_band'][band]}")
    print()
    fixed = out["fixed"]
    print(f"fixed ledger ({fixed['refutations']} refutations): "
          f"{fixed['texts_hit']}/{len(texts)} texts hit "
          f"({fixed['rate_pct']}%), anchor={fixed['fires']['anchor']} "
          f"subject={fixed['fires']['subject']}\n")
    print(f"scaling ({SEEDS} seeds per size):")
    for row in out["scaling"]:
        print(f"  ledger {row['ledger_size']:>3}: mean {row['mean_rate_pct']}% "
              f"median {row['median_rate_pct']}% "
              f"[{row['min_rate_pct']}–{row['max_rate_pct']}] "
              f"p05–p95 [{row['p05_rate_pct']}–{row['p95_rate_pct']}] "
              f"rails={row['fires_by_rail']}")


if __name__ == "__main__":
    if not os.environ.get("DAIMON_CHECKPOINT_DIR"):
        print("set DAIMON_CHECKPOINT_DIR to a scratch directory first",
              file=sys.stderr)
        raise SystemExit(2)
    main()
