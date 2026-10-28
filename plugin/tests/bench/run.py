"""LongMemEval-S benchmark runner (#267).

Usage (from the plugin/ directory, uv-managed env):

    uv run python -m tests.bench.run --suite longmemeval-s --sample 50
    uv run python -m tests.bench.run --suite longmemeval-s --sample 5 --workers 8
    uv run python -m tests.bench.run --suite longmemeval-s --sample 50 --carry

Downloads the dataset on demand (checksum-pinned, never vendored), serializes each
sampled question's haystack through daimon's real pipeline, answers via recall, and
writes a machine-readable JSON result with a full, reproducible config stamp. The
LLM backend/model are read from daimon's own config and RECORDED — the number is
meaningless without them. See benchmark/README.md for the reporting policy.

A run where too many sessions failed to serialize (#344, failed over every
session that reached the serializer, past SERIALIZE_FAILURE_RATIO_MAX) exits
EXIT_SERIALIZE_FAILURES and writes no result:
a results file implies the pipeline ran. `--force-score` writes it anyway,
marked `forced_score`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import config, llm

from tests.bench import adapter, cache as cache_mod
from tests.bench import dataset, metrics

HARNESS_VERSION = "1.0.0"

# #344: the serialize-failure sanity gate. The adapter turns every per-call
# failure into a `failed` tally and keeps going (one bad session must not kill
# a 500-question run), so the runner, the only layer that sees the whole run,
# refuses to score when `failed / (failed + serialized + cached)` exceeds this
# ratio. The denominator is every session that reached the serializer, so the
# ratio is the share of the scored SAMPLE that is missing: a cached session is
# present in the sample exactly like a fresh one, while a too-short session
# never reached the serializer and a #343 error row carries no tally at all.
# A warm re-run with 190 cached, 2 fresh and 1 failed is 1/193 missing, not
# 1/3 of the fresh calls; a cold all-fail run is 1.0 either way. The number
# is a judgment call between two observed points: the committed baseline
# (benchmark/results/longmemeval-s-baseline.json) failed 0 of its 243
# sessions, and the incident behind #344 failed every one (a gateway 401).
# Past one in ten the sample is no longer the sample: every failed session is
# a gold candidate that can only score as a miss. Deliberately NOT a CLI
# flag: a tunable threshold invites "raise it until it passes", which is the
# exact failure the gate exists to stop. `--force-score` is the escape hatch,
# and it marks the report. A run where nothing reached the serializer has no
# ratio and never trips.
SERIALIZE_FAILURE_RATIO_MAX = 0.10
# Distinct from argparse's usage error (2) and SystemExit(str)'s 1, so a
# workflow can tell "the gate fired" from "the dataset is missing".
EXIT_SERIALIZE_FAILURES = 3


class SerializeFailuresExceeded(RuntimeError):
    """#344: the run's serialize-failure ratio is past the gate; nothing was
    written. str() names the counts, the ratio, the threshold and the way out."""

    def __init__(self, gate: "SerializeFailureGate"):
        self.gate = gate
        super().__init__(
            f"aborting: {gate.failed} of {gate.sessions} sessions failed to "
            f"serialize (ratio {gate.ratio or 0.0:.2f} > "
            f"{SERIALIZE_FAILURE_RATIO_MAX:.2f}); the pipeline did not run, so "
            f"no results file was written. Fix the backend, or re-run with "
            f"--force-score to write a report marked forced_score"
        )


@dataclasses.dataclass(frozen=True)
class SerializeFailureGate:
    failed: int
    sessions: int  # failed + serialized + cached: reached the serializer

    @property
    def ratio(self) -> float | None:
        """failed / sessions, or None when no session reached the serializer
        (every one too short): honest absence, never 0.0 read as a clean pass."""
        if self.sessions == 0:
            return None
        return self.failed / self.sessions

    @property
    def exceeded(self) -> bool:
        ratio = self.ratio
        return ratio is not None and ratio > SERIALIZE_FAILURE_RATIO_MAX


def serialize_failure_gate(per_question: list[dict]) -> SerializeFailureGate:
    """Tally the sessions that reached the serializer across the run (#344):
    failed, serialized and cached. Too-short sessions never reached it; rows
    without a `serialize` block (#343 error rows) carry no tally to count."""
    tallies = [r["serialize"] for r in per_question if "serialize" in r]
    failed = sum(t["failed"] for t in tallies)
    sessions = failed + sum(t["serialized"] + t["cached"] for t in tallies)
    return SerializeFailureGate(failed=failed, sessions=sessions)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_DIR = _REPO_ROOT / "benchmark"
_DATA_DIR = _BENCH_DIR / ".data"
_CACHE_DIR = _BENCH_DIR / ".cache"
_RESULTS_DIR = _BENCH_DIR / "results"
_CHECKSUM_FILE = _BENCH_DIR / "longmemeval_s.sha256"


def _effective_backend() -> str:
    """The backend llm.chat will actually dispatch to, mirroring its own routing."""
    backend = config.llm_backend()
    if backend != "auto":
        return backend
    if config.llm_api_key():
        return "litellm"
    if llm._resolve_command() is not None:
        return "command"
    return "litellm"


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _resolve_dataset(args) -> Path:
    """Return the dataset path, downloading + checksum-verifying on demand.

    Checksum is trust-on-first-use: if none is pinned yet, the computed digest is
    written to benchmark/longmemeval_s.sha256 so every later run verifies against
    it. A mismatch is a hard error (corrupt download or changed upstream file).
    """
    if args.dataset_path:
        return Path(args.dataset_path)
    dest = _DATA_DIR / dataset.DATASET_FILENAME
    if not dest.exists():
        if args.no_download:
            raise SystemExit(f"dataset missing at {dest} and --no-download set")
        print(f"downloading LongMemEval-S -> {dest} (~277 MB, one time)...")
        dataset.download(dest, args.url)
    expected = None
    if _CHECKSUM_FILE.exists():
        expected = _CHECKSUM_FILE.read_text(encoding="utf-8").split()[0].strip()
    if not dataset.verify_sha256(dest, expected):
        digest = dataset.sha256_of(dest)
        _CHECKSUM_FILE.write_text(f"{digest}  {dataset.DATASET_FILENAME}\n",
                                  encoding="utf-8")
        print(f"pinned dataset checksum (trust-on-first-use): {digest}")
    return dest


def _build_config_stamp(args, dataset_path: Path) -> dict:
    """Everything needed to reproduce the number — and NOTHING secret (no key/url)."""
    return {
        "harness_version": HARNESS_VERSION,
        "suite": args.suite,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": _git_commit(),
        "backend": _effective_backend(),
        "backend_configured": config.llm_backend(),
        "model": config.llm_model(),
        "prompt_version": adapter.PROMPT_VERSION,
        "seed": args.seed,
        "sample": args.sample,
        "k": args.k,
        "recall_depth": args.depth,
        "min_messages": args.min_messages,
        # Cross-session carry (#274) is a separate retrieval axis: recorded
        # truthfully so carry-on and carry-off numbers are never conflated.
        "carry": "on" if args.carry else "off",
        # Scene traces (#317/#319): same conflation rule as carry — the flag
        # changes the serialize prompt, so the mode is stamped and cache-keyed.
        "scene": "on" if args.scene else "off",
        "workers": args.workers,
        # #343: the explicit served-model expectation (--expect-served /
        # BENCH_EXPECT_SERVED), or null when the run pins on first observation.
        # Recorded so a number can be read knowing whether provenance was
        # asserted up front or adopted from the wire.
        "expected_served": args.expect_served,
        "dataset_file": dataset_path.name,
        "dataset_sha256": dataset.sha256_of(dataset_path),
    }


def _stamp_served_models(stamp: dict, agg: dict, served: list) -> None:
    """#458 / scar 0032: `stamp['model']` is the REQUESTED gateway alias —
    routing config, not provenance (gateways run silent fallback chains, so
    the alias proves nothing about which model ran). `served` is what the
    response bodies actually said across the run (llm.served_models()). One
    distinct name -> `model_served` string; several -> the sorted list AND an
    explicit `mixed_models: true` on the aggregate block, so a mixed run can
    never be attributed to a single model silently. Empty (command backend,
    or an all-cache run that made no LLM call) -> no key at all: honest
    absence, never the alias copied into the served slot."""
    distinct = sorted(set(served))
    if not distinct:
        return
    stamp["model_served"] = distinct[0] if len(distinct) == 1 else distinct
    if len(distinct) > 1:
        agg["mixed_models"] = True


def run(args) -> dict:
    dataset_path = _resolve_dataset(args)
    questions = dataset.sample(dataset.load(dataset_path), args.sample, args.seed)
    # #343: the cache verifies every entry's recorded producer against the
    # run's served-model pin (explicit --expect-served, else first observed).
    cache = cache_mod.CheckpointCache(Path(args.cache_dir),
                                      expected_served=args.expect_served)
    chat = llm.chat

    stamp = _build_config_stamp(args, dataset_path)
    print(f"suite={args.suite} sample={len(questions)} backend={stamp['backend']} "
          f"model={stamp['model']} workers={args.workers} carry={stamp['carry']} "
          f"scene={stamp['scene']}")

    per_question: list[dict] = []
    run_served: set = set()
    t0 = time.monotonic()
    for i, q in enumerate(questions, 1):
        qt0 = time.monotonic()
        # #343: the adapter adds per-capture receipts to this question-scoped
        # set, including receipts from captures that errored.
        try:
            result = adapter.run_question(
                q, chat=chat, cache=cache, backend=stamp["backend"],
                model=stamp["model"] or "unknown", root=Path(args.work_dir),
                k=args.k, depth=args.depth, min_messages=args.min_messages,
                workers=args.workers, carry_on=args.carry, scene_on=args.scene,
                run_served=run_served,
            )
            print(f"[{i}/{len(questions)}] {result['question_id']} "
                  f"hit@{args.k}={result['hit_at_5']} "
                  f"r@{args.k}={result['recall_at_5']} "
                  f"indexed={result['serialize']['indexed']}/{result['n_haystack']} "
                  f"({time.monotonic() - qt0:.0f}s)")
        except cache_mod.ServedModelMismatch as e:
            # #343: fail-loud, not fail-run. The question becomes an error row
            # (never scored, aggregate carries `questions_error`), the run
            # continues — later questions served by the pin still score.
            result = {
                "question_id": q["question_id"],
                "question_type": q.get("question_type"),
                "error": "served_model_mismatch",
                "error_detail": str(e),
                "model_pinned": e.pinned,
                "model_observed": e.observed,
            }
            print(f"[{i}/{len(questions)}] {q['question_id']} "
                  f"ERROR served-model mismatch (#343): {e}")
        per_question.append(result)

    agg = metrics.aggregate(per_question, args.k)
    # #458: stamp what actually served across the whole run (accumulated over
    # per-capture receipts above). A mixed run is flagged loudly in the
    # aggregate block AND on stdout — never aggregated silently.
    _stamp_served_models(stamp, agg, sorted(run_served))
    if agg.get("mixed_models"):
        print(f"WARNING: mixed served models in this run ({stamp['model_served']}) "
              "— do not attribute this number to a single model (#458)")
    if agg.get("questions_error"):
        print(f"WARNING: {agg['questions_error']} question(s) failed loudly on "
              f"served-model mismatch (#343) — recorded as error rows, not "
              f"scored; run pin={cache.pinned_served!r}")
    # #344: the sanity gate sits BEFORE the write. A results file implies the
    # pipeline ran; a run that mostly failed to serialize exits nonzero with
    # nothing on disk, unless --force-score, which writes the report AND marks
    # it so the number can never pass as clean. The tallies are recorded on
    # every report (clean ones too), so a reader can always see the ratio.
    gate = serialize_failure_gate(per_question)
    agg["serialize_failed"] = gate.failed
    agg["serialize_sessions"] = gate.sessions
    agg["serialize_failure_ratio"] = gate.ratio
    if gate.exceeded:
        if not args.force_score:
            raise SerializeFailuresExceeded(gate)
        agg["forced_score"] = True
        print(f"WARNING: {gate.failed} of {gate.sessions} sessions failed to "
              f"serialize (ratio {gate.ratio or 0.0:.2f} > "
              f"{SERIALIZE_FAILURE_RATIO_MAX:.2f}); scoring under --force-score, "
              f"the report is marked forced_score (#344)")
    total_serialized = sum(r["serialize"]["serialized"] for r in per_question
                           if "serialize" in r)
    report = {
        "config": stamp,
        "metrics": agg,
        "cost": {
            "llm_serialize_calls": total_serialized,
            "cache_hits": cache.hits,
            "cache_misses": cache.misses,
            # #343: verification misses broken out of the total so a producer
            # rejection is never mistaken for a cold cache. legacy = pre-#343
            # entries with no recorded producer (one-time re-warm cost).
            "cache_model_mismatch_misses": cache.model_mismatch_misses,
            "cache_legacy_misses": cache.legacy_misses,
            "wall_seconds": round(time.monotonic() - t0, 1),
        },
        "per_question": per_question,
    }

    out = Path(args.out) if args.out else (
        _RESULTS_DIR / f"{args.suite}-{stamp['generated_at'].replace(':', '')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== result ===" + (" (FORCED: serialize-failure gate overridden, "
                                 "#344)" if agg.get("forced_score") else ""))
    print(f"Recall@{args.k}: {_fmt(agg['recall_at_5'])}   "
          f"Hit@{args.k}: {_fmt(agg['hit_at_5'])}   MRR: {_fmt(agg['mrr'])}")
    print(f"avg injected tokens/q (est): {_fmt(agg['avg_injected_tokens'])}")
    # #405: the forbidden-hit rate is reported ALONGSIDE recall, under the same
    # reporting policy (full config stamp above, self-measured only). Shown only
    # when the sample defines forbidden material — a leak rate over zero cases is
    # meaningless. `penalized` is recall floored by leakage on those cases.
    if agg.get("questions_with_forbidden"):
        print(f"forbidden-hit rate: {_fmt(agg['forbidden_hit_rate'])} "
              f"(over {agg['questions_with_forbidden']} cases with forbidden "
              f"material)   Recall@{args.k} (leak-penalized): "
              f"{_fmt(agg['recall_at_5_penalized'])}")
    # #1038: same numbers, split by question_type — a run-wide forbidden-hit
    # rate blends a type that can never leak with one that can.
    for line in _format_by_question_type(agg.get("by_question_type") or {}, args.k):
        print(line)
    print(f"scored={agg['questions_scored']} abstention={agg['questions_abstention']} "
          f"serialize_calls={total_serialized} serialize_failed={gate.failed} "
          f"cache_hits={cache.hits}")
    print(f"wrote {out}")
    return report


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _format_by_question_type(by_type: dict, k: int) -> list[str]:
    """Render the `by_question_type` aggregate as printable lines (#1038).

    Pure and side-effect-free so the table shape is unit-testable without
    running a question through the harness. A type with no forbidden-material
    rows shows a dash in the leak columns, never a 0.0 that reads as a clean
    pass on a dimension that does not apply to that type.
    """
    if not by_type:
        return []
    lines = ["", "--- by question_type ---"]
    lines.append(
        f"{'type':<28} {'n':>5}  {'recall@' + str(k):>10}  {'hit@' + str(k):>8}  "
        f"{'mrr':>8}  {'tokens':>8}  {'leak_rate':>10}  {'recall@' + str(k) + '(leak)':>16}"
    )
    for qtype, m in by_type.items():
        leak_rate = m.get("forbidden_hit_rate")
        penalized = m.get("recall_at_5_penalized")
        leak_col = _fmt(leak_rate) if leak_rate is not None else "-"
        pen_col = _fmt(penalized) if penalized is not None else "-"
        lines.append(
            f"{qtype:<28} {m['count']:>5}  {_fmt(m.get('recall_at_5')):>10}  "
            f"{_fmt(m.get('hit_at_5')):>8}  {_fmt(m.get('mrr')):>8}  "
            f"{_fmt(m.get('avg_injected_tokens')):>8}  {leak_col:>10}  {pen_col:>16}"
        )
    return lines


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="daimon retrieval benchmark")
    p.add_argument("--suite", default="longmemeval-s", choices=["longmemeval-s"])
    p.add_argument("--sample", type=int, default=50,
                   help="questions to run (<=0 = full 500; default 50 smoke tier)")
    p.add_argument("--seed", type=int, default=0, help="sampling seed (determinism)")
    p.add_argument("--k", type=int, default=5, help="Recall@k / Hit@k window")
    p.add_argument("--depth", type=int, default=50,
                   help="recall results fetched per question (MRR sees this depth)")
    p.add_argument("--workers", type=int, default=4,
                   help="concurrent LLM serialize calls per question")
    p.add_argument("--scene", action="store_true",
                   help="serialize with scene traces (#317, DAIMON_SCENE_TRACES=1); "
                        "stamped in the config and cached under separate keys")
    p.add_argument("--carry", action="store_true",
                   help="fold prior checkpoints forward (cross-session carry, "
                        "#274) — a separate retrieval axis; recorded in the "
                        "config stamp and cached under separate keys")
    p.add_argument("--expect-served",
                   default=os.environ.get("BENCH_EXPECT_SERVED"),
                   help="expected served model for the whole run (#343), as "
                        "the wire reports it (response.model) — NOT the "
                        "configured gateway alias, which never matches (scar "
                        "0032). Env: BENCH_EXPECT_SERVED. When set, every "
                        "receipt (including the first) is checked against it; "
                        "when unset, the run pins the first observed serve. "
                        "Any other served model fails that question loudly")
    p.add_argument("--min-messages", default=adapter.BENCH_MIN_MESSAGES,
                   help="serialize floor for the benchmark (product default is 10)")
    p.add_argument("--dataset-path", help="use a local dataset file (skip download)")
    p.add_argument("--url", default=dataset.DATASET_URL, help="dataset download URL")
    p.add_argument("--no-download", action="store_true",
                   help="fail instead of downloading a missing dataset")
    p.add_argument("--cache-dir", default=str(_CACHE_DIR),
                   help="serialized-checkpoint cache (persist across runs)")
    p.add_argument("--work-dir", default=str(_BENCH_DIR / ".work"),
                   help="per-question isolated store + index scratch")
    p.add_argument("--out", help="result JSON path (default benchmark/results/...)")
    p.add_argument("--force-score", action="store_true",
                   help="write a scored report even when more than "
                        f"{SERIALIZE_FAILURE_RATIO_MAX:.0%} of the sessions "
                        "that reached the serializer failed (#344). The report carries "
                        "metrics.forced_score=true; without this flag such a "
                        f"run exits {EXIT_SERIALIZE_FAILURES} and writes nothing")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except SerializeFailuresExceeded as e:
        print(str(e), file=sys.stderr)
        return EXIT_SERIALIZE_FAILURES
    return 0


if __name__ == "__main__":
    sys.exit(main())
