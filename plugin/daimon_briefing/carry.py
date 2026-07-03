"""Deterministic cross-session carry (#33 Phase 2).

Multicycle run-01 (LOGBOOK 2026-07-02) proved LLM re-emission loses whole
items even from lossless input, while exact-copy carry held 1.0 fidelity and
zero first_seen churn. So carry is CODE: fold the previous checkpoint's
unresolved items into the new one verbatim, expire by #78 weight, dedup by
salient-term overlap, label with carried_from. No I/O, no LLM, no env — the
caller injects clock and knobs (scar: a default wall-clock anywhere silently
freezes time math under simulation)."""

import copy

from . import recall, scoring, store

# (section, key, scoring TYPE_RULES type). Beliefs regenerate cheaply and
# active_topic is per-session by definition — neither carries (v1).
_CARRIED_KINDS = (
    ("working_context", "open_questions", "open_question"),
    ("working_context", "recent_decisions", "recent_decision"),
    ("epistemic_snapshot", "uncertainties", "uncertainty"),
)

_MIN_SHARED = 3     # shared salient terms for same-item
_MIN_RATIO = 0.6    # or this fraction of the shorter term list


def _same_item(a_text: str, b_text: str) -> bool:
    """Term-overlap identity: the serializer rewords constantly (run-01), so
    exact text misses twins. Shared >=3 salient terms, or >=60% of the shorter
    list, means same item. Short texts (<2 salient terms) never fuzzy-match —
    the exact-text guard still catches identical ones."""
    a = set(recall.salient_terms(a_text))
    b = set(recall.salient_terms(b_text))
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= _MIN_SHARED or shared / min(len(a), len(b)) >= _MIN_RATIO


def merge(new_cp: dict, prev_cp: dict | None, now: float,
          floor: float = 0.05, cap: int = 8) -> dict:
    """Fold prev_cp's carry-eligible items into a COPY of new_cp.

    Native items are never dropped or reordered — carry only appends, and (on
    a dedup hit) copies the older first_seen onto the native twin so decay age
    survives rewording. Anachronism guard: healing an old session must not
    swallow a newer checkpoint's state.

    No-op paths (non-dict inputs, anachronism guard) return new_cp UNCHANGED,
    not a copy — callers reassign the result immediately, so a defensive
    deepcopy there would just be wasted work."""
    if not isinstance(new_cp, dict) or not isinstance(prev_cp, dict):
        return new_cp
    new_epoch = store._created_epoch(new_cp.get("created"))
    prev_epoch = store._created_epoch(prev_cp.get("created"))
    if new_epoch is not None and prev_epoch is not None and new_epoch < prev_epoch:
        return new_cp

    out = copy.deepcopy(new_cp)
    prev_sid = str(prev_cp.get("session_id") or "")
    for section, key, item_type in _CARRIED_KINDS:
        native = (out.get(section) or {}).get(key)
        if not isinstance(native, list):
            continue
        prev_items = (prev_cp.get(section) or {}).get(key) or []
        native_texts = {i.get("text") for i in native if isinstance(i, dict)}
        carried = []
        for item in prev_items:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            text = item["text"]
            if text in native_texts:
                continue  # exact twin already present (idempotency)
            twin = next((n for n in native if isinstance(n, dict)
                         and _same_item(text, str(n.get("text") or ""))), None)
            if twin is not None:
                # Session re-discussed it: the new wording wins, but the item's
                # AGE does not reset (run-01: 8-12 resets/20 cycles killed the
                # #128 overdue boost). Keep the older birth stamp.
                if item.get("first_seen") and not twin.get("first_seen"):
                    twin["first_seen"] = item["first_seen"]
                elif item.get("first_seen") and twin.get("first_seen"):
                    old = store._created_epoch(item["first_seen"])
                    cur = store._created_epoch(twin["first_seen"])
                    if old is not None and (cur is None or old < cur):
                        twin["first_seen"] = item["first_seen"]
                continue
            if scoring.effective_weight(item, item_type, now) < floor:
                continue  # expired — deterministic exit (noise budget)
            kept = copy.deepcopy(item)
            kept.setdefault("carried_from", prev_sid)
            carried.append(kept)
            native_texts.add(text)  # two identical prev items must carry once
        carried.sort(key=lambda i: scoring.effective_weight(i, item_type, now),
                     reverse=True)
        native.extend(carried[:cap])
    return out
