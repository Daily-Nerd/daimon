"""LongMemEval-S benchmark runner (#267).

Usage (from the plugin/ directory, uv-managed env):

    uv run python -m tests.bench.run --suite longmemeval-s --sample 50
    uv run python -m tests.bench.run --suite longmemeval-s --sample 5 --workers 8

Downloads the dataset on demand (checksum-pinned, never vendored), serializes each
sampled question's haystack through daimon's real pipeline, answers via recall, and
writes a machine-readable JSON result with a full, reproducible config stamp. The
LLM backend/model are read from daimon's own config and RECORDED — the number is
meaningless without them. See benchmark/README.md for the reporting policy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import config, llm

from tests.bench import adapter, cache as cache_mod
from tests.bench import dataset, metrics

HARNESS_VERSION = "1.0.0"

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
        "carry": "off",
        "workers": args.workers,
        "dataset_file": dataset_path.name,
        "dataset_sha256": dataset.sha256_of(dataset_path),
    }


def run(args) -> dict:
    dataset_path = _resolve_dataset(args)
    questions = dataset.sample(dataset.load(dataset_path), args.sample, args.seed)
    cache = cache_mod.CheckpointCache(Path(args.cache_dir))
    chat = llm.chat

    stamp = _build_config_stamp(args, dataset_path)
    print(f"suite={args.suite} sample={len(questions)} backend={stamp['backend']} "
          f"model={stamp['model']} workers={args.workers}")

    per_question: list[dict] = []
    t0 = time.monotonic()
    for i, q in enumerate(questions, 1):
        qt0 = time.monotonic()
        result = adapter.run_question(
            q, chat=chat, cache=cache, backend=stamp["backend"],
            model=stamp["model"] or "unknown", root=Path(args.work_dir),
            k=args.k, depth=args.depth, min_messages=args.min_messages,
            workers=args.workers,
        )
        per_question.append(result)
        print(f"[{i}/{len(questions)}] {result['question_id']} "
              f"hit@{args.k}={result['hit_at_5']} r@{args.k}={result['recall_at_5']} "
              f"indexed={result['serialize']['indexed']}/{result['n_haystack']} "
              f"({time.monotonic() - qt0:.0f}s)")

    agg = metrics.aggregate(per_question, args.k)
    total_serialized = sum(r["serialize"]["serialized"] for r in per_question)
    report = {
        "config": stamp,
        "metrics": agg,
        "cost": {
            "llm_serialize_calls": total_serialized,
            "cache_hits": cache.hits,
            "cache_misses": cache.misses,
            "wall_seconds": round(time.monotonic() - t0, 1),
        },
        "per_question": per_question,
    }

    out = Path(args.out) if args.out else (
        _RESULTS_DIR / f"{args.suite}-{stamp['generated_at'].replace(':', '')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== result ===")
    print(f"Recall@{args.k}: {_fmt(agg['recall_at_5'])}   "
          f"Hit@{args.k}: {_fmt(agg['hit_at_5'])}   MRR: {_fmt(agg['mrr'])}")
    print(f"avg injected tokens/q (est): {_fmt(agg['avg_injected_tokens'])}")
    print(f"scored={agg['questions_scored']} abstention={agg['questions_abstention']} "
          f"serialize_calls={total_serialized} cache_hits={cache.hits}")
    print(f"wrote {out}")
    return report


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.4f}"


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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
