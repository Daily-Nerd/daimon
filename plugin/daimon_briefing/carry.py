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
# Which rail a same-item match came in on (#268). Dedup treats them alike;
# corroboration does not — see _match_path and _record_corroboration's G4.
_MATCH_EXACT = "exact"
_MATCH_ABSOLUTE = "absolute"
_MATCH_RATIO = "ratio"
_GENERIC_DF = 3     # a term shared by >=3 items of one kind is that kind's
                    # vocabulary, not an item's identity. Filtering it out of
                    # dedup stops generic overlap (data/field/validation, the
                    # #13 live specimen) from forging a false merge. Computed
                    # per kind per merge — no static stoplist, so carry stays
                    # language-neutral (es i18n just shipped).

# Quantity-conflict guard (#173): spelled number-words, normalized to the
# value they name. `salient_terms` drops bare digits (<3 chars) and never
# stems, so "ten" and "10" would otherwise never be recognized as the same
# value — this table is what makes them equivalent.
_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_NUMBER_WORDS = {
    "zero": 0, **_UNITS, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, **_TENS, "hundred": 100,
}
# Compound tens+unit ("twenty-five", "twenty five") must combine into ONE
# value (25), not two ({20, 5}) — matched and consumed before the single-word
# pass below so the tens/unit words aren't ALSO counted individually.
_COMPOUND_RE = re.compile(
    r"\b(" + "|".join(_TENS) + r")[\s-]+(" + "|".join(_UNITS) + r")\b",
    re.IGNORECASE)
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
# Optional decimal point: "2.5" is ONE value (2.5), not two ({2, 5}).
_DIGIT_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
# Thousands separator: "1,000" and "1000" must normalize to the same value.
# Lookaround-only substitution (no capture groups) so it composes as a
# pre-pass without disturbing match offsets used elsewhere.
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")


def _quantity_tokens(text: str) -> frozenset:
    """Digit and spelled-number tokens in `text`, normalized to int/float
    values. A hyphenated compound like "ten-week" still yields {10}: `\\b`
    sits on the letter/hyphen boundary same as on whitespace, so the word
    inside survives untouched by the tokenizer split `salient_terms` would
    apply. Thousands separators are stripped first ("1,000" == "1000"), and
    a compound tens+unit word pair ("twenty-five") combines into one value
    (25) instead of being read as two ({20, 5})."""
    text = _THOUSANDS_COMMA_RE.sub("", text)
    values: set = set()
    consumed: list[tuple[int, int]] = []
    for m in _COMPOUND_RE.finditer(text):
        values.add(_TENS[m.group(1).lower()] + _UNITS[m.group(2).lower()])
        consumed.append(m.span())
    for m in _DIGIT_RE.finditer(text):
        raw = m.group(0)
        values.add(float(raw) if "." in raw else int(raw))
    for m in _NUMBER_WORD_RE.finditer(text):
        if any(start <= m.start() < end for start, end in consumed):
            continue  # already folded into a compound match above
        values.add(_NUMBER_WORDS[m.group(0).lower()])
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
        one is still the same item reworded, not an update.

    Also runs inside `_is_reversal_of` and `bind_links` (both call
    `_same_item` on a supersedes link's free-text TARGET against a prev
    item's text). That's normally inert: a target names the OLD item, so its
    quantities equal or are a subset of the prev item's — conflict needs a
    genuinely NEW, differing number, which a same-subject target citation
    doesn't introduce."""
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


def _match_path(a_text: str, b_text: str, generic=frozenset()) -> str:
    """WHICH rail two texts matched on: `_MATCH_ABSOLUTE`, `_MATCH_RATIO`, or
    "" for no match. `_same_item` is this function's boolean face; the path
    itself matters only to corroboration (#268), which trusts the absolute
    rail and refuses the ratio one.

    Term-overlap identity: the serializer rewords constantly (run-01), so
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
    below (an UPDATE's shared frame, not a reworded twin).

    Absolute beats ratio when both hold: a pair clearing >=_MIN_SHARED is
    reported as absolute regardless of the fraction it also happens to pass."""
    if _quantity_conflict(a_text, b_text):
        return ""
    a = set(recall.salient_terms(a_text)) - generic
    b = set(recall.salient_terms(b_text)) - generic
    if len(a) < 2 or len(b) < 2:
        return ""
    shared = len(a & b)
    if shared >= _MIN_SHARED:
        return _MATCH_ABSOLUTE
    if shared / min(len(a), len(b)) >= _MIN_RATIO:
        return _MATCH_RATIO
    return ""


def _same_item(a_text: str, b_text: str, generic=frozenset()) -> bool:
    """Same-item verdict for dedup/twinning — any `_match_path` rail counts.
    See that function for the thresholds and the don't-merge bias behind
    them."""
    return bool(_match_path(a_text, b_text, generic))


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


def _message_ids(item: dict) -> frozenset:
    """The item's bound transcript-message ids as a set, tolerating the shapes
    a hand-edited or pre-#358 checkpoint can hold (absent, non-list, non-str
    entries) — same read-side tolerance as serializer.scoped_haystack."""
    ids = item.get("source_message_ids")
    if not isinstance(ids, list):
        return frozenset()
    return frozenset(i for i in ids if isinstance(i, str) and i)


def _record_corroboration(observed: list, prev_item: dict, native_item: dict,
                          match_path: str, out_sid: str) -> None:
    """Append `(item_id, origin_session, origin_author)` to `observed` when
    this prev/native pair is INDEPENDENT corroboration — and append nothing
    at all otherwise (#268 slice 2). Pure: decides, records, emits nothing.

    Corroboration is the one signal that RAISES trust, so every guard below
    refuses in the same direction: a missed observation costs a boost, a
    forged one costs the axis. Two checkpoints agreeing because one copied
    the other are ONE witness (manufactured corroboration, arXiv 2606.24322).

      G1 bound origin  — prev names its first writer (#268 S1). Never guessed
        from `carried_from`: that names the LAST hop, and on the twin path not
        even that. Unbound stays permanently ineligible.
      G2 different origin — the first writer is not the session doing the
        observing. Both sides required: a checkpoint with no session_id cannot
        prove somebody ELSE wrote the claim, and unprovable is not corroborated.
      G3 witnessed, not echoed — the NATIVE item is this session's own verified
        verbatim. `trust` alone is a claim; `quote_verified is True` is the
        check (and #441 is what stops daimon's own injected briefing text from
        passing it). Read PRE-freeze: the #22 verbatim freeze overwrites the
        native's trust/quote_verified with prev's, so afterwards this reads the
        wrong item entirely.
      G4 strong match only — identical text, or >=_MIN_SHARED shared salient
        terms. The ratio rail exists so short rewordings still MERGE; two
        shared terms out of three is nowhere near evidence of two independent
        statements.
      G5 not a reversal — inherited: reversal pairs never reach the twin block
        (#167). A contradiction is not agreement.
      G6 disjoint message binding — both sides citing the same transcript turn
        is one utterance read twice (a re-serialize, a duplicated transcript).
        Only fires when BOTH carry ids; absent bindings prove nothing either way.

    G7 (the origin session still exists on disk) is deliberately NOT here —
    it needs I/O, so it belongs to the emitter (S3), never to this module.

    The recorded id is the one the pair ENDS UP under: the native's own when
    it has one, else the prev id it inherits on the setdefault rail below (and
    on the exact-text branch the two are equal anyway — ids are sha1 of
    kind:text). Neither side having an id means nothing to attribute the
    observation to, so nothing is recorded. `origin_author` is optional
    (hosts that name no author): absent becomes "", never a fabricated name."""
    origin = str(prev_item.get("origin_session") or "")
    if not origin:
        return                                                          # G1
    if not out_sid or origin == out_sid:
        return                                                          # G2
    if (native_item.get("trust") != "verbatim"
            or native_item.get("quote_verified") is not True):
        return                                                          # G3
    if match_path not in (_MATCH_EXACT, _MATCH_ABSOLUTE):
        return                                                          # G4
    prev_msgs, native_msgs = _message_ids(prev_item), _message_ids(native_item)
    if prev_msgs & native_msgs:
        return                                                          # G6
    item_id = str(native_item.get("id") or prev_item.get("id") or "")
    if not item_id:
        return  # no identity to attribute the observation to
    observed.append((item_id, origin, str(prev_item.get("origin_author") or "")))


def merge(new_cp: dict, prev_cp: dict | None, now: float,
          floor: float = 0.05, cap: int = 8,
          resolved: frozenset = frozenset(),
          observed: list | None = None) -> dict:
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

    `observed` (#268 slice 2): an optional list merge APPENDS corroboration
    observations to — `(item_id, origin_session, origin_author)` per prev/
    native pair that clears `_record_corroboration`'s guards. Out-parameter
    rather than a second return value so no existing caller changes; default
    None skips the predicate entirely, so behaviour is byte-identical to
    before for everyone who doesn't ask. Observation only: this module still
    writes no ledger, emits no event and renders nothing (that is S3/S4), and
    the merged checkpoint is the same either way.

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
    # The OBSERVING session — who is agreeing, for the corroboration predicate's
    # G2. Read off the checkpoint being merged into (it is stamped at
    # serialize, long before this runs); absent means G2 is unprovable and
    # every observation refuses.
    out_sid = str(out.get("session_id") or "")
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
                # #268: this branch returns before the twin block, so the
                # STRONGEST pair there is — a later session restating the
                # identical sentence — would never be seen. Match against the
                # natives only: `native_texts` also holds texts carried in
                # THIS call, and a prev item agreeing with another prev item
                # is not a witness. The reversal check the twin block gets by
                # construction (#167) has to be explicit here.
                exact = next((n for n in native if isinstance(n, dict)
                              and n.get("text") == text), None)
                if exact is not None:
                    # #268 S1: the kept copy is the NATIVE one, a fresh item.
                    # Without inheritance here, bind_origin at the next write
                    # names the RESTATING session as first writer — origin of
                    # record must ride the exact rail exactly as it rides the
                    # twin rail (same setdefault, never re-bound).
                    for field in ("origin_session", "origin_author"):
                        if item.get(field):
                            exact.setdefault(field, item[field])
                    if observed is not None and not _is_reversal_of(
                            exact, text, item.get("id"), generic):
                        _record_corroboration(observed, item, exact,
                                              _MATCH_EXACT, out_sid)
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
                # #268: the corroboration verdict runs FIRST — BEFORE the
                # freeze below, which overwrites the native twin's trust and
                # drops its quote_verified, i.e. destroys exactly the evidence
                # G3 asks for (did THIS session witness the claim, or merely
                # reword what it was handed?).
                if observed is not None:
                    _record_corroboration(
                        observed, item, twin,
                        _match_path(text, str(twin.get("text") or ""), generic),
                        out_sid)
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
                    # F4 (#527): `because` explains ITS text. The freeze
                    # restores prev's wording, so prev's reasoning rides
                    # along and the twin's own — written for a sentence that
                    # no longer renders — must not survive attached to it.
                    if item.get("because"):
                        twin["because"] = item["because"]
                    else:
                        twin.pop("because", None)
                    if item.get("quote"):
                        twin["quote"] = item["quote"]
                        # source_message_ids travel WITH the quote (#358),
                        # same rail as quote_verified below: a binding
                        # attests THIS quote's origin message. The twin's
                        # own ids described its now-replaced quote — keeping
                        # them would bind prev's quote to the wrong turn.
                        if item.get("source_message_ids"):
                            twin["source_message_ids"] = (
                                item["source_message_ids"])
                        else:
                            twin.pop("source_message_ids", None)
                        # quote_verified travels WITH the quote (#167): the
                        # native's verdict attested its own (now replaced)
                        # quote — keeping it would stamp "verified" on a quote
                        # this session never checked. Only True rides along
                        # (#209): False is a fresh-only signal (see the carry
                        # path below), and post-#125 a verbatim item cannot
                        # legally hold it — but pre-#125 and hand-edited
                        # checkpoints exist, so anything but True (False,
                        # malformed) collapses to absent = unknown, same as a
                        # pre-#125 checkpoint.
                        if item.get("quote_verified") is True:
                            twin["quote_verified"] = True
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
                # last_verified (#215): the OPPOSITE bias from first_seen —
                # NEWER wins, not older. first_seen is a birth stamp (age must
                # never reset); last_verified is a world-check stamp. A native
                # twin's own last_verified, when present, is ALWAYS freshest
                # by construction — it can only have been stamped by THIS
                # session's #125 verify_quotes run (carry folds prev into a
                # checkpoint that already went through verify_quotes), so it
                # is never overwritten. Only when the twin carries no stamp of
                # its own does prev's older one propagate (still better than
                # nothing). Checkpoints are append-only: this is carry's one
                # READ of last_verified, never a write-back onto prev — prev's
                # own copy is left untouched.
                if item.get("last_verified") and not twin.get("last_verified"):
                    twin["last_verified"] = item["last_verified"]
                # Identity rides the same rail as first_seen (#102): the prev
                # item's id lands on the reworded native twin, so a resolution
                # recorded against the old id still binds after re-extraction.
                if item.get("id"):
                    twin.setdefault("id", item["id"])
                # Origin (#268) rides that same rail, and the twin path is the
                # ONLY place it needs a line: plain carry deep-copies the whole
                # prev item, binding included. A reworded twin is not a copy —
                # without this it would reach write_checkpoint unbound and
                # bind_origin would name THIS session as the first writer, so
                # every rewording would mint a fresh witness and a claim
                # restated across N sessions would read as N independent
                # agreements. Absent on a pre-#268 prev item -> left unbound
                # here (write_checkpoint binds it), never an empty stamp.
                for field in ("origin_session", "origin_author"):
                    if item.get(field):
                        twin.setdefault(field, item[field])
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
            # quote_verified:false is a FRESH-ONLY signal (#209): it asserts
            # THIS serialize's verify_quotes failed the item, which a carried
            # copy never ran — inheriting it makes checkpoint-level metrics
            # double-count one failure forever. Origin checkpoint keeps the
            # stamp (and the retained quote/trust, untouched here) for
            # forensics; the copy reverts to absent = unverified/unknown.
            # `is False` only — never touch True (a real attestation, #167)
            # or malformed values (not carry's mess to clean).
            if kept.get("quote_verified") is False:
                kept.pop("quote_verified")
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
