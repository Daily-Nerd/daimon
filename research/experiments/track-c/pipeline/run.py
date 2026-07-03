# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Track C — epistemic-graph pipeline runner + scorer.

Implements the D-005 architecture and scores it against a human-labeled run:

  Stage 2 (temporal KG): each claim gets a validity interval. A later claim
    with a DIFFERENT stance on the SAME subject SUPERSEDES the earlier one
    (closes its interval) — that is belief EVOLUTION, not contradiction.
  Stage 3 (flag): a contradiction is flagged ONLY when two same-subject,
    different-stance claims have OVERLAPPING validity intervals (half-open).

A "baseline arm" flags every same-subject/different-stance pair regardless of
time (the naive raw approach) so we can measure the pipeline's LIFT.

Metrics: BEP (extraction precision), FCR (false-contradiction rate),
EMR (evolution-misclassification rate), and lift vs baseline. Prints the
Build/Pivot/Kill verdict.

Usage:
    uv run run.py ../fixtures/evolution-pairs.json      # synthetic self-test
    uv run run.py ../runs/S1xS3.run.json                # a real run

Run-file shape: see fixtures/evolution-pairs.json and scoring/run.template.json.
"""

import argparse
import json
import sys
from itertools import combinations

INF = float("inf")


def interval(claim: dict, claims: list[dict]) -> tuple[float, float]:
    """Validity interval [start, end) for a claim.

    - explicit: use provided start/end (end null -> +inf)
    - point: [t, t] treated as [t, t] (overlaps only co-located points)
    - ongoing: [t, next-superseding-claim.t) ; +inf if never superseded
      Superseded by the EARLIEST later claim on the same subject with a
      DIFFERENT stance.
    """
    v = claim.get("validity", {"type": "ongoing"})
    t = claim["timestamp"]
    kind = v.get("type", "ongoing")
    if kind == "explicit":
        end = v.get("end")
        return (v.get("start", t), INF if end is None else end)
    if kind == "point":
        return (t, t)
    # ongoing -> find supersession
    later = [
        c["timestamp"] for c in claims
        if c["subject"] == claim["subject"]
        and c["stance"] != claim["stance"]
        and c["timestamp"] > t
    ]
    return (t, min(later)) if later else (t, INF)


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Half-open overlap, with point intervals [t,t] treated as inclusive."""
    a0, a1 = a
    b0, b1 = b
    if a0 == a1 or b0 == b1:        # at least one point interval
        return a0 <= b1 and b0 <= a1
    return a0 < b1 and b0 < a1      # half-open [start, end)


def conflict_pairs(claims: list[dict]) -> list[tuple[str, str]]:
    """All same-subject, different-stance unordered claim pairs."""
    out = []
    for a, b in combinations(claims, 2):
        if a["subject"] == b["subject"] and a["stance"] != b["stance"]:
            out.append(tuple(sorted((a["id"], b["id"]))))
    return out


def pipeline_flags(claims: list[dict]) -> set[tuple[str, str]]:
    by_id = {c["id"]: c for c in claims}
    flags = set()
    for pid in conflict_pairs(claims):
        a, b = by_id[pid[0]], by_id[pid[1]]
        if overlaps(interval(a, claims), interval(b, claims)):
            flags.add(pid)
    return flags


def baseline_flags(claims: list[dict]) -> set[tuple[str, str]]:
    return set(conflict_pairs(claims))


def norm_pairs(pairs) -> set[tuple[str, str]]:
    return {tuple(sorted(p)) for p in pairs}


def score_arm(flags, gold, evolution):
    false_flags = flags - gold
    fcr = len(false_flags) / len(flags) if flags else 0.0
    emr = len(flags & evolution) / len(evolution) if evolution else 0.0
    recall = len(flags & gold) / len(gold) if gold else None
    return fcr, emr, recall, false_flags


def pct(x):
    return "  n/a" if x is None else f"{x * 100:5.1f}%"


def verdict(bep, fcr, emr, lift_clear) -> str:
    if fcr >= 0.40 or emr >= 0.40 or not lift_clear:
        return "KILL"
    if bep >= 0.75 and fcr <= 0.20 and emr <= 0.20 and lift_clear:
        return "PASS — BUILD"
    return "PIVOT"


def main() -> int:
    ap = argparse.ArgumentParser(description="Track C pipeline runner + scorer")
    ap.add_argument("runfile", help="run JSON (fixture or real)")
    args = ap.parse_args()

    with open(args.runfile) as f:
        doc = json.load(f)

    claims = doc["extracted_claims"]
    gold = norm_pairs(doc.get("gold_contradictions", []))
    evolution = norm_pairs(doc.get("evolution_pairs", []))

    # BEP — extraction precision (did the disambiguation gate keep only beliefs?)
    n_extracted = len(claims)
    n_beliefs = sum(1 for c in claims if c.get("is_belief", True))
    bep = n_beliefs / n_extracted if n_extracted else 0.0

    pf = pipeline_flags(claims)
    bf = baseline_flags(claims)
    p_fcr, p_emr, p_rec, p_false = score_arm(pf, gold, evolution)
    b_fcr, b_emr, b_rec, b_false = score_arm(bf, gold, evolution)

    fcr_lift = b_fcr - p_fcr
    emr_lift = b_emr - p_emr
    lift_clear = (p_fcr < b_fcr) or (p_emr < b_emr)

    print(f"\nTrack C — Epistemic-Graph Pipeline  ({doc.get('session_pair', args.runfile)})\n")
    print(f"Extraction:  {n_beliefs}/{n_extracted} kept as beliefs  ->  BEP {pct(bep)}")
    print(f"Subject-conflict pairs considered: {len(conflict_pairs(claims))}\n")

    hdr = f"{'arm':<12}{'flags':>7}{'FCR':>9}{'EMR':>9}{'recall':>9}"
    print(hdr)
    print("-" * len(hdr))
    print(f"{'pipeline':<12}{len(pf):>7}{pct(p_fcr):>9}{pct(p_emr):>9}{pct(p_rec):>9}")
    print(f"{'baseline':<12}{len(bf):>7}{pct(b_fcr):>9}{pct(b_emr):>9}{pct(b_rec):>9}")

    print(f"\nLift (baseline - pipeline):  FCR {fcr_lift * 100:+.1f} pts   EMR {emr_lift * 100:+.1f} pts")
    if p_false:
        print(f"Pipeline false flags: {sorted(p_false)}")
    if pf & evolution:
        print(f"Pipeline flagged EVOLUTION pairs (bad): {sorted(pf & evolution)}")

    v = verdict(bep, p_fcr, p_emr, lift_clear)
    print(f"\n{'=' * 50}\nVERDICT: {v}\n{'=' * 50}")
    print("Bars: PASS = BEP>=75% & FCR<=20% & EMR<=20% & lift; "
          "KILL = FCR>=40% | EMR>=40% | no lift.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
