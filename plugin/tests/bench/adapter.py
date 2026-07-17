"""The benchmark adapter seam (#267).

Runs ONE LongMemEval question through daimon's real pipeline, with no shortcuts:
every haystack session is serialized by `serializer.serialize_strict` (the same
call the SessionEnd hook makes) and written to the store by `store.write_checkpoint`;
the question is then answered only by what `recall.search` surfaces from that index.

Isolation: each question gets its own checkpoint dir + recall index under a temp
root, addressed through the same DAIMON_* env vars the test suite uses. Carry
defaults off so each session's checkpoint stands alone (a clean session→id
mapping for scoring); the min-messages floor is lowered so short evidence
sessions still enter the index (the product default of 10 would skip ~half the
haystack — recorded in the run config, and called out in the README).

Carry-on (#274) mirrors the product's SessionEnd path (cli._run_serialize):
after each raw serialize, the previous checkpoint is read back via
store.read_latest and folded forward with carry.merge before the write. The
fold is order-dependent (each session must see the one before it), but the LLM
serialize is NOT — serialize_strict reads only its own transcript — so the
expensive concurrent step stays concurrent and the fold runs in the
single-threaded, listed-session-order write loop below. That reproduces the
product's strictly-sequential semantics deterministically at any worker count;
no worker forcing is needed.

The expensive step (the LLM serialize) runs concurrently across sessions; the
store writes are serialized under a lock because pointer rotation and GC are not
concurrency-safe. Recall indexes the per-session flat files, not the pointers, so
serializing the writes costs nothing in fidelity.
"""

from __future__ import annotations

import contextlib
import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from daimon_briefing import carry, config, recall, serializer, store

from tests.bench import cache as cache_mod
from tests.bench import dataset, metrics

PROMPT_VERSION = serializer.PROMPT_VERSION

# Every session enters the index for the benchmark (the product's live default is
# 10; sessions shorter than that are skipped in production — a real limitation,
# recorded in the run config and the README, not hidden by the harness).
BENCH_MIN_MESSAGES = "2"

_ENV_KEYS = (
    "DAIMON_CHECKPOINT_DIR", "DAIMON_RECALL_DB", "DAIMON_LOG_DIR",
    "DAIMON_RECALL_SEEN_DIR", "DAIMON_TEAM_DIR", "DAIMON_PROJECT_DIR",
    "DAIMON_CARRY", "DAIMON_CARRY_FLOOR", "DAIMON_CARRY_MAX",
    "DAIMON_MIN_MESSAGES", "DAIMON_TEAM", "DAIMON_DISABLE",
    "DAIMON_RECEIPTS", "DAIMON_SCAR_HARVEST", "DAIMON_SCENE_TRACES",
)


def _question_env(root: Path, qid: str, min_messages: str,
                  carry_on: bool = False,
                  scene_on: bool = False) -> dict[str, str]:
    """The DAIMON_* environment that isolates one question's store + index."""
    home = root / _safe(qid)
    project = home / "project"
    return {
        "DAIMON_CHECKPOINT_DIR": str(home / "checkpoints"),
        "DAIMON_RECALL_DB": str(home / "recall.db"),
        "DAIMON_LOG_DIR": str(home / "logs"),
        "DAIMON_RECALL_SEEN_DIR": str(home / "recall_seen"),
        "DAIMON_TEAM_DIR": str(home / "team"),
        "DAIMON_PROJECT_DIR": str(project),
        # off: per-session checkpoints stand alone. on (#274): the write loop
        # folds prior checkpoints forward, mirroring the product path.
        "DAIMON_CARRY": "1" if carry_on else "0",
        # Carry knobs pinned to the product defaults so an ambient override on
        # the host can never change the measurement (determinism).
        "DAIMON_CARRY_FLOOR": "0.05",
        "DAIMON_CARRY_MAX": "8",
        "DAIMON_MIN_MESSAGES": min_messages,
        "DAIMON_TEAM": "0",              # no team dual-write during the benchmark
        "DAIMON_DISABLE": "0",
        "DAIMON_RECEIPTS": "0",          # receipts add cost, not retrieval signal
        "DAIMON_SCAR_HARVEST": "0",
        # #319: pinned explicitly — the host's env file may carry the #317
        # field experiment's flag, and process env overrides it; an unpinned
        # baseline would silently serialize with scenes.
        "DAIMON_SCENE_TRACES": "1" if scene_on else "0",
    }


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)


@contextlib.contextmanager
def _env(mapping: dict[str, str]):
    """Set env vars for the block, restoring prior values (or unsetting) after."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update(mapping)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _created_stamp(index: int) -> str:
    """A deterministic, strictly-increasing `created` stamp per haystack index.

    Recency is only a recall tiebreak (bm25 relevance dominates), so the exact
    times do not matter — determinism does. Later-listed sessions read as newer.
    """
    minute = index % 60
    hour = (index // 60) % 24
    return f"2024-01-01T{hour:02d}:{minute:02d}:00Z"


def serialize_question(question: dict, *, chat, cache: cache_mod.CheckpointCache,
                       backend: str, model: str, project_dir: str,
                       workers: int, carry_on: bool = False,
                       scene_on: bool = False) -> tuple[dict, dict]:
    """Serialize every haystack session into the (already isolated) store.

    Returns (tally, attribution):
      tally       — {serialized, cached, too_short, failed, indexed}
      attribution — (checkpoint session_id, item text) -> origin session id, for
                    every indexed item. With carry off this is the identity map;
                    with carry on, a carried copy maps to `carried_from` (the
                    session that first produced it — chains preserve the origin
                    because carry.merge stamps carried_from with setdefault).
    """
    sessions = dataset.sessions_of(question)
    cache_lock = threading.Lock()
    carry_mode = "on" if carry_on else "off"
    scene_mode = "on" if scene_on else "off"

    def _produce(item):
        index, (sid, messages) = item
        key = cache_mod.cache_key(messages, backend=backend, model=model,
                                  prompt_version=PROMPT_VERSION, carry=carry_mode,
                                  scene=scene_mode)
        with cache_lock:
            cached = cache.get(key)
        if cached is not None:
            return index, sid, cached, "cached"
        try:
            checkpoint = serializer.serialize_strict(sid, messages, chat=chat)
        except serializer.TooShortError:
            return index, sid, None, "too_short"
        except serializer.SerializeError:
            return index, sid, None, "failed"
        except Exception:
            return index, sid, None, "failed"
        with cache_lock:
            cache.put(key, checkpoint)
        return index, sid, checkpoint, "serialized"

    workers = max(1, min(workers, len(sessions) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        produced = list(pool.map(_produce, enumerate(sessions)))

    tally = {"serialized": 0, "cached": 0, "too_short": 0, "failed": 0, "indexed": 0}
    attribution: dict[tuple[str, str], str] = {}
    # Store writes are single-threaded: pointer rotation + GC are not concurrency-safe.
    # With carry on this loop is ALSO the ordering seam: each session folds in the
    # checkpoint written just before it (listed-session order), so carry-on output
    # is deterministic even though the LLM serialize above ran concurrently — the
    # raw serialize has no dependence on prior state (see the module docstring).
    for index, sid, checkpoint, status in sorted(produced, key=lambda r: r[0]):
        tally[status] += 1
        if checkpoint is None:
            continue
        cp = copy.deepcopy(checkpoint)
        cp["session_id"] = sid
        cp["created"] = _created_stamp(index)
        if carry_on:
            # Product-faithful fold, mirroring cli._run_serialize (#33 Phase 2):
            # prev = this project's own latest only (fallback=False, #94), clock =
            # this checkpoint's deterministic stamp, knobs from config (pinned in
            # _question_env), resolutions folded the same way (empty in the
            # isolated bench store, read anyway for fidelity).
            prev = store.read_latest(project_dir, fallback=False)
            now = store._created_epoch(cp["created"]) or 0.0
            events = store.resolutions(project_dir=project_dir)
            resolved = frozenset(ref for ref, evt in events.items()
                                 if store.is_resolved(evt))
            cp = carry.merge(cp, prev, now, floor=config.carry_floor(),
                             cap=config.carry_max(), resolved=resolved)
            store._stamp_item_ids(cp)
            carry.bind_links(cp, prev)
        store.write_checkpoint(sid, cp, project_dir=project_dir)
        tally["indexed"] += 1
        # Attribution is harvested AFTER the write: write_checkpoint mutates the
        # checkpoint in place (redaction, id stamps), and recall indexes the
        # written text — the map must key on exactly what recall will return.
        for item in serializer.iter_items(cp):
            text = str(item.get("text") or "")
            if not text:
                continue
            origin = str(item.get("carried_from") or "") or sid
            key2 = (sid, text)
            if key2 in attribution and attribution[key2] != origin:
                # Same text native in one kind and carried in another: ambiguous.
                # Never guess — fall back to the hosting session (can only
                # under-credit gold, never over-credit it).
                attribution[key2] = sid
            else:
                attribution[key2] = origin
    return tally, attribution


def recall_question(question: dict, *, project_dir: str, depth: int) -> list[dict]:
    """Answer the question with recall over this question's index (top-`depth`)."""
    return recall.search(question["question"], project_dir=project_dir, limit=depth)


def run_question(question: dict, *, chat, cache: cache_mod.CheckpointCache,
                 backend: str, model: str, root: Path, k: int, depth: int,
                 min_messages: str = BENCH_MIN_MESSAGES, workers: int = 1,
                 carry_on: bool = False, scene_on: bool = False) -> dict:
    """Full per-question pipeline: isolate → serialize haystack → recall → score."""
    env = _question_env(root, question["question_id"], min_messages, carry_on,
                        scene_on)
    project_dir = env["DAIMON_PROJECT_DIR"]
    with _env(env):
        tally, attribution = serialize_question(
            question, chat=chat, cache=cache, backend=backend, model=model,
            project_dir=project_dir, workers=workers, carry_on=carry_on,
            scene_on=scene_on,
        )
        results = recall_question(question, project_dir=project_dir, depth=depth)

    gold = dataset.gold_sessions(question)
    abstention = dataset.is_abstention(question)
    ranked = metrics.attributed_sessions(results, attribution)
    return {
        "question_id": question["question_id"],
        "question_type": question.get("question_type"),
        "abstention": abstention,
        "n_haystack": len(question.get("haystack_session_ids") or []),
        "n_gold": len(gold),
        "serialize": tally,
        "n_retrieved_sessions": len(ranked),
        "recall_at_5": metrics.recall_at_k(ranked, gold, k),
        "hit_at_5": metrics.hit_at_k(ranked, gold, k),
        "mrr": metrics.reciprocal_rank(ranked, gold),
        "injected_tokens": metrics.injected_tokens(results, k),
    }
