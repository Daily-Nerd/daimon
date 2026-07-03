"""Deterministic grading: substring survival, difflib integrity, V1-after-flip
staleness, first_seen persistence. No LLM judges by design — the scale test
nearly shipped a false verdict from a grading artifact.

staleness = old value present WITHOUT the new one — the evolution-noted form
("revised from V1 to V2") is correct memory, not staleness (run-01 audit,
same artifact class as the scale-test's verbose-answer trap)."""

import difflib
import json
from collections import defaultdict

import seed

_SECTIONS = (
    ("working_context", "active_topic"),
    ("working_context", "open_questions"),
    ("working_context", "recent_decisions"),
    ("epistemic_snapshot", "strong_beliefs"),
    ("epistemic_snapshot", "uncertainties"),
    ("epistemic_snapshot", "contradictions_flagged"),
)


def _walk_items(cp):
    """Yield (section_key, item_dict) for every item in the checkpoint."""
    for sec, key in _SECTIONS:
        block = cp.get(sec) or {}
        raw = block.get(key)
        items = [raw] if key == "active_topic" else (raw or [])
        for item in items:
            if isinstance(item, dict) and item.get("text"):
                yield key, item


def _find(cp, tokens):
    """Best (section, item) whose text/quote contains any grading token."""
    for key, item in _walk_items(cp):
        hay = (str(item.get("text", "")) + " " + str(item.get("quote", ""))).lower()
        if any(t.lower() in hay for t in tokens):
            return key, item
    return None, None


def grade_checkpoint(cp: dict, cycle: int, arm: str) -> list[dict]:
    rows = []
    for item_key, tokens in seed.NONCES.items():
        section, item = _find(cp, tokens)
        survived = item is not None
        integrity = 0.0
        stale = False
        if survived:
            integrity = round(difflib.SequenceMatcher(
                None, seed.SEED_TEXTS[item_key], item["text"]).ratio(), 3)
            if item["text"] == seed.SEED_TEXTS[item_key]:
                integrity = 1.0
        if item_key == "FACT-EVOLVE" and cycle >= seed.FLIP_CYCLE:
            blob = json.dumps(cp).lower()
            stale = (seed.V1_TOKEN.lower() in blob
                     and seed.V2_TOKEN.lower() not in blob)
        rows.append({
            "arm": arm, "cycle": cycle, "item": item_key,
            "survived": survived, "section": section,
            "integrity": integrity, "stale": stale,
            "first_seen": item.get("first_seen") if survived else None,
            "importance": item.get("importance") if survived else None,
            "trust": item.get("trust") if survived else None,
        })
    return rows


def summarize(rows: list[dict]) -> str:
    """Markdown: per (arm, item) — last surviving cycle, final integrity,
    stale-cycle count, first_seen resets (stamp changed vs previous cycle)."""
    by = defaultdict(list)
    for r in rows:
        by[(r["arm"], r["item"])].append(r)
    lines = ["| arm | item | last cycle alive | final integrity | stale cycles | first_seen resets |",
             "| --- | --- | --- | --- | --- | --- |"]
    for (arm, item), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["cycle"])
        alive = [r["cycle"] for r in rs if r["survived"]]
        last_alive = max(alive) if alive else -1
        final_integrity = next((r["integrity"] for r in reversed(rs)
                                if r["survived"]), 0.0)
        stale_n = sum(1 for r in rs if r["stale"])
        resets = 0
        prev = None
        for r in rs:
            if r["survived"] and prev is not None and r["first_seen"] != prev:
                resets += 1
            if r["survived"]:
                prev = r["first_seen"]
        lines.append(f"| {arm} | {item} | {last_alive} | {final_integrity} "
                     f"| {stale_n} | {resets} |")
    return "\n".join(lines)
