"""Ungated-arm replay (#754): measure the trust gate's retrieval cost.

The benchmark README asserts that daimon "trades some raw recall for
verifiability". This runner measures that trade with zero LLM calls by
replaying the frozen per-question stores a completed bench run leaves under
`benchmark/.work/` (the stores behind a committed `results/*.json` file):

  gated arm    rebuild the recall index from each store's checkpoints as
               written, run `recall.search`, score with the bench metrics
  ungated arm  in a copy of the store, revert every trust-gate downgrade
               (see `should_flip`), rebuild, re-run the same searches

Registered prediction (frozen in #754 before the run): identical rankings,
because `verify_quotes` / `ground_outcomes` only rewrite labels — `text` and
`quote`, the only fields FTS5 indexes, survive a downgrade untouched — and
`search()` does not rank on trust.

Copy-on-read: the `.work` originals are never written; both arms run against
copies (current `recall._ensure_fresh` rebuilds any index it is pointed at).
Run from the repo root:

  cd plugin && uv run python ../research/experiments/ungated-arm/replay.py

Requires a completed bench run's `.work` stores for every question in the
reference results file; sessions are never re-serialized (no LLM).
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO = _HERE.parents[3]
PLUGIN = REPO / "plugin"
sys.path.insert(0, str(PLUGIN))

from daimon_briefing import recall, serializer  # noqa: E402
from tests.bench import dataset, metrics  # noqa: E402

WORK = REPO / "benchmark" / ".work"
DATA = REPO / "benchmark" / ".data" / dataset.DATASET_FILENAME
REFERENCE = REPO / "benchmark" / "results" / "interim-317-baseline-first54.json"
OUT = _HERE.parent / "measurements.json"
K = 5
DEPTH = 50
MIN_MESSAGES = 2  # the reference run's bench floor

_ENV_KEYS = (
    "DAIMON_CHECKPOINT_DIR", "DAIMON_RECALL_DB", "DAIMON_LOG_DIR",
    "DAIMON_RECALL_SEEN_DIR", "DAIMON_TEAM_DIR", "DAIMON_PROJECT_DIR",
    "DAIMON_CARRY", "DAIMON_MIN_MESSAGES", "DAIMON_TEAM", "DAIMON_DISABLE",
    "DAIMON_RECEIPTS", "DAIMON_SCAR_HARVEST", "DAIMON_SCENE_TRACES",
)


def _env_for(home: Path) -> dict:
    return {
        "DAIMON_CHECKPOINT_DIR": str(home / "checkpoints"),
        "DAIMON_RECALL_DB": str(home / "recall.db"),
        "DAIMON_LOG_DIR": str(home / "logs"),
        "DAIMON_RECALL_SEEN_DIR": str(home / "recall_seen"),
        "DAIMON_TEAM_DIR": str(home / "team"),
        "DAIMON_PROJECT_DIR": str(home / "project"),
        "DAIMON_CARRY": "0",
        "DAIMON_MIN_MESSAGES": str(MIN_MESSAGES),
        "DAIMON_TEAM": "0",
        "DAIMON_DISABLE": "0",
        "DAIMON_RECEIPTS": "0",
        "DAIMON_SCAR_HARVEST": "0",
        "DAIMON_SCENE_TRACES": "0",
    }


class _Env:
    def __init__(self, mapping):
        self.mapping = mapping

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(self.mapping)

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def should_flip(item: dict) -> bool:
    """True when the trust gate downgraded this item from verbatim.

    Both downgrade paths leave a code-owned marker no model output carries
    into a fresh item: `verify_quotes` stamps `quote_verified: false`,
    `ground_outcomes` stamps `grounded: false` (and only ever on items that
    WERE trust=verbatim). Natively-inferred items carry neither marker.
    """
    if item.get("trust") != "inferred":
        return False
    return item.get("quote_verified") is False or item.get("grounded") is False


def flip_downgrades(home: Path) -> int:
    """Revert trust-gate downgrades in every checkpoint file. Returns flips."""
    flipped = 0
    for p in (home / "checkpoints").rglob("*.json"):
        try:
            cp = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cp, dict):
            continue
        changed = False
        for item in serializer.iter_items(cp):
            if should_flip(item):
                item["trust"] = "verbatim"
                flipped += 1
                changed = True
        if changed:
            p.write_text(json.dumps(cp, ensure_ascii=False), encoding="utf-8")
    db = home / "recall.db"
    if db.exists():
        db.unlink()
    return flipped


def indexed_sessions(db_path: Path) -> set:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in
                conn.execute("SELECT DISTINCT session_id FROM items")}
    finally:
        conn.close()


def replay_store(home: Path, question: dict) -> dict:
    with _Env(_env_for(home)):
        recall.rebuild()
        idx = indexed_sessions(home / "recall.db")
        results = recall.search(question["question"], all_projects=True,
                                limit=DEPTH)
    ranked = metrics.attributed_sessions(results, {})
    gold = dataset.gold_sessions(question)
    first_gold_rank = next(
        (i + 1 for i, sid in enumerate(ranked) if sid in gold), None)
    return {
        "indexed": idx,
        "ranked": ranked,
        "recall_at_5": metrics.recall_at_k(ranked, gold, K),
        "hit_at_5": metrics.hit_at_k(ranked, gold, K),
        "mrr": metrics.reciprocal_rank(ranked, gold),
        "first_gold_rank": first_gold_rank,
    }


def classify_gold(sid, ranked, gold_state):
    """One gold session's fate under an arm's ranking."""
    if sid in ranked[:K]:
        return "hit_top5"
    if sid in ranked:
        return "indexed_deep"
    if gold_state == "indexed":
        return "indexed_unretrieved"
    if gold_state == "too_short":
        return "absent_too_short"
    return "absent_missing"


def main():
    questions = {q["question_id"]: q for q in dataset.load(DATA)}
    ref = {q["question_id"]: q
           for q in json.load(open(REFERENCE))["per_question"]}

    scratch = Path(tempfile.mkdtemp(prefix="ungated-arm-"))
    rows = []
    for n, qid in enumerate(ref, 1):
        q = questions[qid]
        src = WORK / qid
        if not src.is_dir():
            raise SystemExit(f"{qid}: no .work store; run the bench first")
        gated_home = scratch / qid / "gated"
        flip_home = scratch / qid / "ungated"
        for dst in (gated_home, flip_home):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)

        sessions = dataset.sessions_of(q)
        gold = dataset.gold_sessions(q)

        gated = replay_store(gated_home, q)
        n_flips = flip_downgrades(flip_home)
        ungated = replay_store(flip_home, q)

        idx = gated["indexed"]
        acct = {"indexed": 0, "too_short": 0, "missing": 0}
        gold_state = {}
        for sid, messages in sessions:
            if sid in idx:
                state = "indexed"
            elif len(messages) < MIN_MESSAGES:
                state = "too_short"
            else:
                state = "missing"
            acct[state] += 1
            if sid in gold:
                gold_state[sid] = state

        rows.append({
            "question_id": qid,
            "question_type": q.get("question_type"),
            "abstention": dataset.is_abstention(q),
            "n_sessions": len(sessions),
            "accounting": acct,
            "n_gold": len(gold),
            "gold_states": {sid: classify_gold(sid, gated["ranked"],
                                               gold_state.get(sid))
                            for sid in gold},
            "gated": {k: gated[k] for k in
                      ("recall_at_5", "hit_at_5", "mrr", "first_gold_rank")},
            "ungated": {k: ungated[k] for k in
                        ("recall_at_5", "hit_at_5", "mrr", "first_gold_rank")},
            "n_flipped_items": n_flips,
            "rank_identical": gated["ranked"] == ungated["ranked"],
            "ref": {k: ref[qid].get(k) for k in
                    ("recall_at_5", "hit_at_5", "mrr")},
        })
        print(f"[{n:2d}/{len(ref)}] {qid} gated R@5={gated['recall_at_5']} "
              f"ungated R@5={ungated['recall_at_5']} flips={n_flips} "
              f"identical={rows[-1]['rank_identical']}", flush=True)

    scored = [r for r in rows if not r["abstention"]]
    summary = {
        "questions": len(rows),
        "scored": len(scored),
        "rank_identical": sum(r["rank_identical"] for r in rows),
        "flipped_items": sum(r["n_flipped_items"] for r in rows),
        "gated_recall_at_5": round(sum(r["gated"]["recall_at_5"]
                                       for r in scored) / len(scored), 3),
        "ungated_recall_at_5": round(sum(r["ungated"]["recall_at_5"]
                                         for r in scored) / len(scored), 3),
        "questions_with_metric_delta": sum(r["gated"] != r["ungated"]
                                           for r in rows),
    }
    OUT.write_text(json.dumps(
        {"issue": 754, "summary": summary, "per_question": rows},
        indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
