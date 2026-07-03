# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Track A scorer — computes Recall Rate (RR), False-Memory Rate (FMR),
Omission Rate (OR) per session and in aggregate, splits recall by trust
class (D-006 signal), reports the staleness rate (Q-STALE signal), and
prints the Build/Pivot/Kill verdict.

Usage:
    uv run score.py runs/*/session-*.score.json
    uv run score.py runs/S1/session-S1.score.json runs/S2/session-S2.score.json

Bars (docs/VALIDATION.md, Track A):
    Pass  : mean RR >= 0.70 AND mean FMR <= 0.10  (and not a kill)
    Kill  : FMR >= 0.20 on >= 2 sessions  OR  mean RR < 0.50
    Pivot : anything in between (notably FMR in (0.10, 0.20])

Staleness (Q-STALE, advisory — never gates the verdict):
    staleness = recalled items pinned to a SUPERSEDED in-session state
                / recalled items graded for staleness (`stale` key present)
    Bar: mean staleness <= 0.10 (provisional, mirrors the FMR bar).
    Stale items still count as recalled for RR — they ARE recalled, just
    pinned to the wrong state — so historical RR numbers stay comparable.
    Legacy score files without `stale` keys report "no data" and score
    identically to before.
"""

import argparse
import glob
import json
import sys
from dataclasses import dataclass


STALENESS_BAR = 0.10  # provisional, mirrors the FMR bar; ADVISORY only


@dataclass
class SessionScore:
    sid: str
    rr: float
    fmr: float
    omr: float
    n_gt: int
    n_claims: int
    n_false: int
    rr_verbatim: float | None
    rr_inferred: float | None
    staleness: float | None
    n_pinnable: int
    n_stale: int


def score_session(doc: dict) -> SessionScore:
    sid = doc.get("session_id", "?")
    gt = [i for i in doc.get("ground_truth_items", []) if "recalled" in i]
    claims = doc.get("reconstruction_claims", [])

    n_gt = len(gt)
    n_recalled = sum(1 for i in gt if i["recalled"])
    rr = n_recalled / n_gt if n_gt else 0.0
    omr = 1.0 - rr if n_gt else 0.0

    n_claims = len(claims)
    n_false = sum(1 for c in claims if not c.get("grounded", False))
    fmr = n_false / n_claims if n_claims else 0.0

    # Q-STALE: of the RECALLED items the grader checked for staleness
    # (`stale` key present), how many are pinned to a superseded in-session
    # state? Stale items stay recalled for RR. No `stale` keys -> no data.
    pinnable = [i for i in gt if i["recalled"] and "stale" in i]
    n_pinnable = len(pinnable)
    n_stale = sum(1 for i in pinnable if i["stale"])
    staleness = n_stale / n_pinnable if n_pinnable else None

    def rr_for(trust: str) -> float | None:
        subset = [i for i in gt if i.get("trust") == trust]
        if not subset:
            return None
        return sum(1 for i in subset if i["recalled"]) / len(subset)

    return SessionScore(
        sid=sid, rr=rr, fmr=fmr, omr=omr, n_gt=n_gt,
        n_claims=n_claims, n_false=n_false,
        rr_verbatim=rr_for("verbatim"), rr_inferred=rr_for("inferred"),
        staleness=staleness, n_pinnable=n_pinnable, n_stale=n_stale,
    )


def pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}%"


def staleness_lines(scores: list[SessionScore]) -> list[str]:
    """Q-STALE advisory summary. Never gates the verdict — gates stay RR/FMR."""
    with_data = [s for s in scores if s.staleness is not None]
    if not with_data:
        return ["staleness: no data (no recalled items carry a `stale` grade — "
                "legacy score files, or no evolving facts graded)"]
    mean_st = sum(s.staleness for s in with_data) / len(with_data)
    mark = "PASS" if mean_st <= STALENESS_BAR else "FAIL"
    return [f"mean staleness = {pct(mean_st)} over {len(with_data)} of {len(scores)} sessions "
            f"[ADVISORY {mark}, bar <= {STALENESS_BAR * 100:.0f}%; does not gate the verdict]"]


def verdict(scores: list[SessionScore]) -> tuple[str, list[str]]:
    n = len(scores)
    mean_rr = sum(s.rr for s in scores) / n
    mean_fmr = sum(s.fmr for s in scores) / n
    n_high_fmr = sum(1 for s in scores if s.fmr >= 0.20)
    notes = [
        f"mean RR = {pct(mean_rr)}",
        f"mean FMR = {pct(mean_fmr)}",
        f"sessions with FMR >= 20% : {n_high_fmr} of {n}",
    ]
    if n_high_fmr >= 2 or mean_rr < 0.50:
        return "KILL", notes
    if mean_rr >= 0.70 and mean_fmr <= 0.10:
        return "PASS — BUILD", notes
    return "PIVOT", notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Track A confabulation scorer")
    ap.add_argument("files", nargs="+", help="session score JSON files (globs ok)")
    ap.add_argument("--cycle", action="store_true",
                    help="label output as a cycle-degradation run (compare FMR to the base run by eye)")
    args = ap.parse_args()

    paths: list[str] = []
    for pattern in args.files:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        print("No score files matched.", file=sys.stderr)
        return 2

    scores: list[SessionScore] = []
    for p in paths:
        with open(p) as f:
            doc = json.load(f)
        s = score_session(doc)
        if s.n_gt == 0:
            print(f"WARNING: {p} has no scored ground-truth items — skipped.", file=sys.stderr)
            continue
        scores.append(s)

    if not scores:
        print("No scorable sessions.", file=sys.stderr)
        return 2

    tag = " (CYCLE-DEGRADATION RUN)" if args.cycle else ""
    print(f"\nTrack A — CRP Confabulation Results{tag}\n")
    hdr = (f"{'session':<10}{'RR':>8}{'FMR':>8}{'OR':>8}{'stale':>8}"
           f"{'GT':>5}{'claims':>8}{'false':>7}   {'RR(verb)':>9}{'RR(infer)':>10}")
    print(hdr)
    print("-" * len(hdr))
    for s in scores:
        print(f"{s.sid:<10}{pct(s.rr):>8}{pct(s.fmr):>8}{pct(s.omr):>8}{pct(s.staleness):>8}"
              f"{s.n_gt:>5}{s.n_claims:>8}{s.n_false:>7}   "
              f"{pct(s.rr_verbatim):>9}{pct(s.rr_inferred):>10}")

    v, notes = verdict(scores)
    print("\nAggregate:")
    for nline in notes:
        print(f"  - {nline}")
    for nline in staleness_lines(scores):
        print(f"  - {nline}")

    # D-006 signal
    vs = [s.rr_verbatim for s in scores if s.rr_verbatim is not None]
    ins = [s.rr_inferred for s in scores if s.rr_inferred is not None]
    if vs and ins:
        mv, mi = sum(vs) / len(vs), sum(ins) / len(ins)
        delta = mv - mi
        print(f"\nD-006 (extractive pinning): verbatim RR {pct(mv)} vs inferred RR {pct(mi)} "
              f"(delta {delta * 100:+.1f} pts)")
        if delta > 0.10:
            print("  -> verbatim-pinned items recalled materially better. Supports D-006.")
        elif delta < -0.10:
            print("  -> inferred recalled better (unexpected). Revisit D-006.")
        else:
            print("  -> no strong trust-class effect. Inconclusive on D-006.")

    print(f"\n{'=' * 48}\nVERDICT: {v}\n{'=' * 48}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
