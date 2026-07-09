"""Deterministic cross-session carry (#33 Phase 2).

Multicycle run-01 (LOGBOOK 2026-07-02) proved LLM re-emission loses whole
items even from lossless input, while exact-copy carry held 1.0 fidelity and
zero first_seen churn. So carry is CODE: fold the previous checkpoint's
unresolved items into the new one verbatim, expire by #78 weight, dedup by
salient-term overlap, label with carried_from. No I/O, no LLM, no env — the
caller injects clock and knobs (scar: a default wall-clock anywhere silently
freezes time math under simulation)."""

import copy
import re
from collections import Counter

from . import recall, schema, scoring, store

# An item id already looks like this (store._stamp_item_ids: kind-initial +
# >=6 hex chars, optional -N collision suffix) — never treat it as free text
# to rebind. Bounded quantifiers only (scar: unbounded prefix before an
# alternation froze the write path under quadratic backtracking).
_ID_SHAPE = re.compile(r"[a-z]-[0-9a-f]{6,}(-\d+)?")

# (section, key, scoring TYPE_RULES type), from the shared schema (#146).
# Beliefs regenerate cheaply and active_topic is per-session by definition —
# neither carries (v1); the carries flag in schema.ITEM_FIELDS records that.
_CARRIED_KINDS = schema.CARRIED_KINDS

_MIN_SHARED = 3     # shared salient terms for same-item
_MIN_RATIO = 0.6    # or this fraction of the shorter term list
_GENERIC_DF = 3     # a term shared by >=3 items of one kind is that kind's
                    # vocabulary, not an item's identity. Filtering it out of
                    # dedup stops generic overlap (data/field/validation, the
                    # #13 live specimen) from forging a false merge. Computed
                    # per kind per merge — no static stoplist, so carry stays
                    # language-neutral (es i18n just shipped).

# Quantity-conflict guard (#173): spelled number-words, normalized to the
# digit they name. `salient_terms` drops bare digits (<3 chars) and never
# stems, so "ten" and "10" would otherwise never be recognized as the same
# value — this table is what makes them equivalent.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\b\d+\b")


def _quantity_tokens(text: str) -> frozenset:
    """Digit and spelled-number tokens in `text`, normalized to int values.
    A hyphenated compound like "ten-week" still yields {10}: `\\b` sits on
    the letter/hyphen boundary same as on whitespace, so the word inside
    survives untouched by the tokenizer split `salient_terms` would apply."""
    values = {int(m.group(0)) for m in _DIGIT_RE.finditer(text)}
    values.update(_NUMBER_WORDS[m.group(0).lower()]
                  for m in _NUMBER_WORD_RE.finditer(text))
    return frozenset(values)


def _quantity_conflict(a_text: str, b_text: str) -> bool:
    """Structural evidence two texts are DISTINCT items regardless of term
    overlap (#173): both carry quantity tokens, and neither's value set is a
    subset of the other's — a two-sided numeric mismatch (the live specimen:
    {10, 6} vs {6, 3}, "10" and "3" each appear on only one side).

    Two deliberate non-fires, both to avoid re-breaking #22:
      - either text has NO quantity tokens -> no conflict. A restatement can
        drop every number; that must stay mergeable.
      - one set is a SUBSET of the other ({6} vs {3, 6}) -> no conflict. A
        restatement that drops SOME numbers but introduces no new, differing
        one is still the same item reworded, not an update."""
    a = _quantity_tokens(a_text)
    b = _quantity_tokens(b_text)
    if not a or not b:
        return False
    return not (a <= b or b <= a)


def _generic_terms(texts, k: int = _GENERIC_DF) -> frozenset:
    """Salient terms appearing in >= k DISTINCT texts of one kind — that kind's
    shared vocabulary, which dedup must ignore. Document frequency counts a term
    once per text (set per text), so repetition inside one item can't inflate
    it."""
    df: Counter = Counter()
    for t in texts:
        df.update(set(recall.salient_terms(t)))
    return frozenset(term for term, n in df.items() if n >= k)


def _same_item(a_text: str, b_text: str, generic=frozenset()) -> bool:
    """Term-overlap identity: the serializer rewords constantly (run-01), so
    exact text misses twins. Shared >=3 salient terms, or >=60% of the shorter
    list, means same item — but only AFTER subtracting `generic` (the kind's
    document-frequent vocabulary), so overlap on common words can't merge
    unrelated items.

    Floor: if either filtered set has <2 terms, never fuzzy-match. This blocks a
    single surviving shared term from passing the ratio path (1/1 = 1.0). The
    bias is deliberate and asymmetric: a false merge erases a loop and forges
    its birth stamp, while a false non-merge only costs a duplicate item — so
    tie-break toward NOT merging. The exact-text guard still catches identical
    items regardless.

    Quantity-conflict guard (#173) runs FIRST and short-circuits term overlap
    entirely: two texts stating different numbers are structurally distinct
    even when they share enough subject vocabulary to clear the thresholds
    below (an UPDATE's shared frame, not a reworded twin)."""
    if _quantity_conflict(a_text, b_text):
        return False
    a = set(recall.salient_terms(a_text)) - generic
    b = set(recall.salient_terms(b_text)) - generic
    if len(a) < 2 or len(b) < 2:
        return False
    shared = len(a & b)
    return shared >= _MIN_SHARED or shared / min(len(a), len(b)) >= _MIN_RATIO


def _is_reversal_of(native_item: dict, prev_text: str, prev_id,
                    generic=frozenset()) -> bool:
    """Reversal signature (#167): a native item that carries its OWN verified
    quote AND a `supersedes` link aimed at the prev item is a REVERSAL of that
    item, not a re-statement — merge's twin block must not treat it as a twin
    (the #22 freeze would overwrite the reversal with the very decision it
    reverses, and twin id-inheritance would make bind_links suppress the
    supersede-candidate event as a self-link).

    Both legs are required: the verified quote pins the reversal to THIS
    session's transcript (an unverified quote could be fabricated re-extraction
    — freeze stays the safe default), and the link must aim at the prev item
    (id-equal when already bound, `_same_item` on free text otherwise) so an
    unrelated supersedes link can't defeat the freeze."""
    if native_item.get("quote_verified") is not True:
        return False
    links = native_item.get("links")
    if not isinstance(links, list):
        return False
    for link in links:
        if not isinstance(link, dict) or link.get("type") != "supersedes":
            continue
        target = link.get("target")
        if not isinstance(target, str) or not target.strip():
            continue
        if _ID_SHAPE.fullmatch(target):
            if prev_id and target == prev_id:
                return True
            continue
        if _same_item(target, prev_text, generic):
            return True
    return False


def merge(new_cp: dict, prev_cp: dict | None, now: float,
          floor: float = 0.05, cap: int = 8,
          resolved: frozenset = frozenset()) -> dict:
    """Fold prev_cp's carry-eligible items into a COPY of new_cp.

    Native items are never dropped or reordered — carry only appends, and (on
    a dedup hit) copies the older first_seen onto the native twin so decay age
    survives rewording. Anachronism guard: healing an old session must not
    swallow a newer checkpoint's state.

    `resolved` (#102): a set of item_ref/id strings the CALLER has already
    determined are closed (via store.resolutions/is_resolved) — merge does no
    I/O itself (module invariant, see the module docstring). A resolved prev
    item with NO native twin is not carried. A resolved prev item WITH a
    native twin still runs the twin block: id inheritance still lands on the
    twin (that's what lets #103 suppress the re-extraction at render time),
    and the native item itself is never dropped — only the render layer, not
    carry, decides what to do with a resolved-but-still-mentioned item.

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
        # Generic vocabulary for THIS kind, from the same universe merge iterates
        # (native + prev): terms this common are not identity, so dedup ignores
        # them (#13). Computed once per kind, passed to every _same_item below.
        generic = _generic_terms(
            [str(i.get("text") or "") for i in native if isinstance(i, dict)]
            + [str(i.get("text") or "") for i in prev_items if isinstance(i, dict)])
        carried = []
        for item in prev_items:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            text = item["text"]
            if text in native_texts:
                continue  # exact twin already present (idempotency)
            # Reversal guard (#167): a native that supersedes THIS prev item
            # (own verified quote + aimed link) is excluded from twin candidacy
            # — no freeze, no id inheritance, and the prev item falls through
            # to the normal carry path below so the render layer can flag it
            # once bind_links emits the supersede-candidate event.
            twin = next((n for n in native if isinstance(n, dict)
                         and _same_item(text, str(n.get("text") or ""), generic)
                         and not _is_reversal_of(n, text, item.get("id"),
                                                 generic)),
                        None)
            if twin is not None:
                # Session re-discussed it. Split by the PREV item's trust class
                # (#22, two-path recall):
                #   - verbatim -> FREEZE. A verbatim item carries an immutable
                #     pinned quote (D-006); recall must not re-write it
                #     (reconsolidation, Nader/Schafe/LeDoux 2000). The prev's
                #     frozen original text+quote+trust overwrite the reworded
                #     native twin. prev is the older by construction and holds
                #     the canonical pin, so it wins even when the native twin is
                #     itself verbatim with a DIFFERENT quote (don't-erode; the
                #     asymmetric bias in _same_item — false-merge worse than
                #     false-non-merge — favors the original). external_state and
                #     other native fields are left untouched.
                #   - inferred/untagged -> new wording wins (beliefs are allowed
                #     to reconsolidate; that is correct).
                # AGE never resets either way (run-01: 8-12 resets/20 cycles
                # killed the #128 overdue boost) — keep the older birth stamp.
                if item.get("trust") == "verbatim":
                    twin["text"] = item["text"]
                    if item.get("quote"):
                        twin["quote"] = item["quote"]
                        # quote_verified travels WITH the quote (#167): the
                        # native's verdict attested its own (now replaced)
                        # quote — keeping it would stamp "verified" on a quote
                        # this session never checked. Prev's verdict rides
                        # along; absent (pre-#125 checkpoint) means unknown.
                        if "quote_verified" in item:
                            twin["quote_verified"] = item["quote_verified"]
                        else:
                            twin.pop("quote_verified", None)
                    twin["trust"] = "verbatim"
                if item.get("first_seen") and not twin.get("first_seen"):
                    twin["first_seen"] = item["first_seen"]
                elif item.get("first_seen") and twin.get("first_seen"):
                    old = store._created_epoch(item["first_seen"])
                    cur = store._created_epoch(twin["first_seen"])
                    if old is not None and (cur is None or old < cur):
                        twin["first_seen"] = item["first_seen"]
                # Identity rides the same rail as first_seen (#102): the prev
                # item's id lands on the reworded native twin, so a resolution
                # recorded against the old id still binds after re-extraction.
                if item.get("id"):
                    twin.setdefault("id", item["id"])
                continue
            if item.get("id") in resolved:
                continue  # world closed this loop (#102) — stop carrying it
            if scoring.effective_weight(item, item_type, now) < floor:
                continue  # expired — deterministic exit (noise budget)
            # Carry-once covers REWORDED twins too (#31 item 9): a prev item
            # that fuzzy-matches something already carried this call is the
            # same loop reworded — first (prev-order) wording wins.
            if any(_same_item(text, str(c.get("text") or ""), generic)
                   for c in carried):
                continue
            kept = copy.deepcopy(item)
            kept.setdefault("carried_from", prev_sid)
            carried.append(kept)
            native_texts.add(text)  # two identical prev items must carry once
        carried.sort(key=lambda i: scoring.effective_weight(i, item_type, now),
                     reverse=True)
        native.extend(carried[:cap])
    return out


def bind_links(merged_cp: dict, prev_cp: dict | None) -> list[tuple[str, str, str]]:
    """Pure text-target -> prev-id binding (#14). For every `supersedes` link
    on merged_cp's carried-kind items whose `target` is still free text (not
    already an item-id shape), find the SAME-KIND prev item it refers to by
    `_same_item` and rewrite `link["target"]` to that item's id — IN PLACE on
    merged_cp (caller owns the copy, mirrors `merge`'s contract).

    Never-guess: only a UNIQUE prev match rewrites; zero or multiple matches
    leave the text target untouched (same don't-merge bias as `_same_item` —
    a wrong bind fabricates provenance, a missed bind just stays text).
    Self/twin guard: skip when the matched prev id equals the item's own id
    (twin id-inheritance in `merge` makes a decision supersede itself
    reachable). Malformed links (non-dict, missing/non-str target) are
    skipped, never raised on.

    Returns (old_id, new_id, old_text) triples for the caller to turn into
    events — no I/O here, same as `merge`. Deduped by (old_id, new_id): two
    links resolving to the same prev item are one supersession event."""
    if not isinstance(merged_cp, dict) or not isinstance(prev_cp, dict):
        return []
    pairs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()  # dedupe triples by (old_id, new_id) —
    # two links resolving to the same prev item are one supersession event
    for section, key, _item_type in _CARRIED_KINDS:
        native = (merged_cp.get(section) or {}).get(key)
        if not isinstance(native, list):
            continue
        prev_items = [p for p in ((prev_cp.get(section) or {}).get(key) or [])
                      if isinstance(p, dict) and p.get("id")
                      and str(p.get("text") or "").strip()]
        if not prev_items:
            continue
        # DF universe: natives + prev, but SKIP carried natives — merge()
        # copied those verbatim from prev, so counting them again doubles
        # their vocabulary's document frequency, forges generic status for
        # terms two prev candidates legitimately share, and collapses an
        # ambiguous target into a false-unique bind.
        generic = _generic_terms(
            [str(i.get("text") or "") for i in native
             if isinstance(i, dict) and not i.get("carried_from")]
            + [str(p["text"]) for p in prev_items])
        for item in native:
            if not isinstance(item, dict) or not isinstance(item.get("links"), list):
                continue
            for link in item["links"]:
                if not isinstance(link, dict) or link.get("type") != "supersedes":
                    continue
                target = link.get("target")
                if not isinstance(target, str) or not target.strip():
                    continue
                if _ID_SHAPE.fullmatch(target):
                    continue  # already bound
                matches = [p for p in prev_items
                           if _same_item(target, str(p["text"]), generic)]
                if not matches:
                    # Loose-target fallback (#168): supersession pairs share
                    # their subject vocabulary BY NATURE — when it reaches
                    # DF>=3 across the kind (reversal + restatement + prev
                    # original), generic subtraction strips exactly the terms
                    # that identify the target and pass 1 finds nothing.
                    # Retry on FULL vocabulary, strict >=3 shared floor only
                    # (no ratio path — terse targets over-fire it); the
                    # unique-match gate below still refuses ambiguity, so a
                    # generic-vocab target matching several items stays text.
                    # Zero matches only: pass 1 finding SEVERAL is a verdict
                    # (ambiguous), not a miss.
                    matches = [p for p in prev_items
                               if len(set(recall.salient_terms(target))
                                      & set(recall.salient_terms(str(p["text"]))))
                               >= _MIN_SHARED]
                if len(matches) != 1:
                    continue  # unbound or ambiguous — leave as text
                prev_id, old_text = matches[0]["id"], matches[0]["text"]
                if prev_id == item.get("id"):
                    continue  # self/twin supersession — no-op, not a link
                link["target"] = prev_id  # every matched link rebinds,
                new_id = item.get("id") or ""
                if (prev_id, new_id) not in seen:  # but one event per pair
                    seen.add((prev_id, new_id))
                    pairs.append((prev_id, new_id, old_text))
    return pairs
