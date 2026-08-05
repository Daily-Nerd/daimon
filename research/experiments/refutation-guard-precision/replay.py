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
import sys
import tempfile

from daimon_briefing import refutations

SEED = 573
LEDGER_SIZES = (6, 20, 60)
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


def _seed(project: str, records) -> None:
    for subject, scope, anchor in records:
        refutations.assert_refutation(
            subject=subject, verdict="measured and rejected", scope=scope,
            evidence=["measurement:replay"], anchors=[anchor],
            authority="human", ratified=True, project_dir=project)


def _replay(project: str, texts: list[str], active: set[str] | None = None):
    fires = {"anchor": 0, "subject": 0}
    hit_texts = 0
    per_subject: dict[str, int] = {}
    for text in texts:
        hits = refutations.guard(text[:1900], project_dir=project)
        if active is not None:
            hits = [h for h in hits
                    if h["guard_match"]["rail"] == "subject"
                    or set(h["guard_match"]["anchors"]) & active]
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

    out: dict = {"corpus_texts": len(texts), "seed": SEED}

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

    random.seed(SEED)
    numbers = random.sample(range(1, ISSUE_RANGE), max(LEDGER_SIZES))
    with tempfile.TemporaryDirectory() as scale_project:
        _seed(scale_project, [
            (f"rejected approach number {i} in the daimon serialize path",
             f"scope {i}", f"issue:{n}")
            for i, n in enumerate(numbers)])
        out["scaling"] = []
        for size in LEDGER_SIZES:
            active = {f"issue:{n}" for n in numbers[:size]}
            fires, hit_texts, _ = _replay(scale_project, texts, active)
            out["scaling"].append({
                "ledger_size": size,
                "fires": fires["anchor"] + fires["subject"],
                "texts_hit": hit_texts,
                "rate_pct": round(100 * hit_texts / len(texts), 3),
            })

    here = pathlib.Path(__file__).parent
    (here / "measurements.json").write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"corpus (non-daimon projects): {len(texts)} item texts\n")
    fixed = out["fixed"]
    print(f"fixed ledger ({fixed['refutations']} refutations): "
          f"{fixed['texts_hit']}/{len(texts)} texts hit "
          f"({fixed['rate_pct']}%), anchor={fixed['fires']['anchor']} "
          f"subject={fixed['fires']['subject']}\n")
    print("scaling:")
    for row in out["scaling"]:
        print(f"  ledger {row['ledger_size']:>3}: {row['fires']:>3} fires on "
              f"{row['texts_hit']:>3}/{len(texts)} texts ({row['rate_pct']}%)")


if __name__ == "__main__":
    if not os.environ.get("DAIMON_CHECKPOINT_DIR"):
        print("set DAIMON_CHECKPOINT_DIR to a scratch directory first",
              file=sys.stderr)
        raise SystemExit(2)
    main()
