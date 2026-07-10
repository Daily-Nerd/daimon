"""Transcript -> cognitive checkpoint (D-010 prompt; chunked armC for long sessions).

Two entry points:
  serialize_strict() — raises a named SerializeError subclass on failure, so
    callers (CLI, logs) can say WHAT failed instead of a conflated guess.
  serialize()        — never-raise wrapper returning None (the hermes hook
    contract). Same behavior as Slice 1.

Transcripts whose rendered text exceeds DAIMON_CHUNK_LINES go through chunked
multi-pass extraction (per-chunk D-007 serialize -> 01c merge), the armC
pipeline from the D-007 probe: single-pass recall fell off a cliff ~1,400
lines; chunking lifted long-session recall ~55% -> ~93% in probe runs.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, llm, redact, schema

# No handlers/basicConfig here — the library stays silent unless the caller
# configures logging. Multi-hour serialize runs need this heartbeat to be killable.
log = logging.getLogger(__name__)


# Serialize-prompt version. Bumped D-008 -> D-010 (#101: emotional_valence
# dropped from the schema; D-009 is taken by the host-adapter decision).
# D-012 -> D-013 (#208: quote copy-paste discipline rule).
# Checkpoints are only comparable across runs sharing this version (scar
# landmine #4); pre-bump checkpoints firing the #93 format_version mismatch
# warning is desired, not a bug.
PROMPT_VERSION = "D-013"


class SerializeError(Exception):
    """Base for named serialization failures. str(e) is log/CLI-ready."""


class TooShortError(SerializeError):
    pass


class LLMCallError(SerializeError):
    pass


class OutputParseError(SerializeError):
    pass


class SchemaValidationError(SerializeError):
    pass

# Adapted from research/experiments/track-a/prompts/01b-serialize-d007.md (D-008),
# minus emotional_valence (dropped in D-010, #101).
# Schema note: "worker_queue" is the deliberate Level-0 initiative placeholder —
# captured by the serializer but intentionally unrendered by the briefing (#101).
SERIALIZE_SYS = """You are ending a work session and must serialize your cognitive state into a strict JSON checkpoint, so a future session can resume.

Output ONLY valid JSON conforming to the schema below. No prose before or after.

RULES — follow every one exactly; this is the point of the exercise:

1. Extract only what the transcript supports. Do NOT invent open questions, decisions, beliefs, or facts not actually present.

2. For every item, set `trust`:
   - "verbatim" -> directly supported by an explicit statement. You MUST copy the exact `quote` from the transcript (see rule 17: QUOTE DISCIPLINE).
   - "inferred" -> you are paraphrasing or synthesizing. Leave `quote` empty.
   Prefer "verbatim" wherever an explicit statement exists.

3. open_questions = things left genuinely unresolved at end. recent_decisions = explicit choices made.
   Be exhaustive on BOTH — they are load-bearing.

4. strong_beliefs / uncertainties = stated positions and stated doubts. Do NOT extract hedges, hypotheticals, sarcasm, or thinking-aloud as beliefs.

5. If unsure whether something belongs, leave it out. Omission is safer than fabrication.

--- D-007 EXTRACTION TARGETS ---

6. ASSISTANT-SIDE FIXES & DIAGNOSES: When the assistant diagnosed a bug, root-caused a failure, or
   applied a fix, extract these as recent_decisions and/or beliefs — even if the USER never explicitly
   stated them. Label clearly: use the prefix "[Fix]" or "[Diagnosis]" in the text.
   Include: what was broken, what the root cause was, and what fix was applied.
   Quote the most direct statement from the transcript (the AI's own diagnosis line if present).

7. IMPLEMENTATION-LEVEL DECISIONS: Extract decisions that were made DURING implementation —
   function names, data structures chosen, algorithmic approaches, library choices, test strategy.
   These appear in assistant turns, not just user-stated choices. Include them in recent_decisions.

8. OPEN END-OF-SESSION QUESTIONS & LOOSE THREADS: Beyond explicit user questions, scan for:
   - Things the assistant said it would do "next" or "after"
   - Verifications that did not happen
   - Optional follow-ups explicitly flagged
   - Anything left ambiguous or deferred to the next session
   Add these to open_questions with trust="verbatim" if quoted, "inferred" if synthesized.

9. PRESERVE D-006 EXTRACTIVE PINNING: For every decision, fix, and open question that has a
    direct quote, you MUST set trust="verbatim" and include that exact quote in the `quote` field.
    Never paraphrase when a direct quote exists.

10. EXTERNAL-STATE FLAG: For any open_question whose answer could have changed OUTSIDE this
    session (a PR the user said they'd merge, a deploy, a file edited elsewhere, an action the
    user took in another tool), add `"external_state": true` to that item. This marks facts the
    next session MUST verify before trusting.

11. FINAL-STATE RESOLUTION: Classify every item by its LAST state in the transcript, not its first. If something raised as an open question earlier is explicitly answered or chosen later — INCLUDING by a terse user ratification ("yes", "go with X", "do it", "sounds good") that covers one or more proposals — record it as a recent_decision, NOT an open_question. Do NOT invent a resolution: promote to a decision only when the transcript explicitly settles it; if it was merely discussed and left hanging, it stays an open_question.

12. DISTINCT ITEMS — DO NOT MERGE: Two decisions, or two uncertainties, that differ in substance are SEPARATE items even when they share a topic. One dropped product idea is not another dropped product idea; a platform you skipped is not an unresolved API-approval for that platform. Extract each distinct choice or doubt as its own item; never collapse several into one summary line.

13. EXACT QUANTITIES & IDENTIFIERS: Copy counts, file ranges, version numbers, commit hashes, ports, and identifiers EXACTLY as the transcript states them (17 files / docs 01-17 / commit 2e1d78b / port 6638 — never "about 15" or "several"). Never round, approximate, or drop a precise quantity the transcript states.

14. IMPORTANCE: score every item's `importance` as an integer 1-10 — how load-bearing it is for
    resuming this work. 1-3 = minor detail, safely forgettable; 4-6 = useful context; 7-8 = changes
    what the next session does; 9-10 = architectural or hard to reverse. Score by CONSEQUENCE,
    not by how recently it was said.

15. TRANSCRIPT LANGUAGE: write every item's `text` in the same language as the transcript
    (a Spanish session produces Spanish items). Never translate quotes — a `quote` is always
    the exact original wording. Schema keys and structure stay in English as shown.

16. SUPERSESSION LINKS (conservative): when a recent_decision explicitly REPLACES a prior
    decision — signaled by explicit replacement language such as "instead of", "replaces", or
    "we changed from X to Y" — attach `"links": [{"type": "supersedes", "target": "<the OLD
    decision, named as specifically as this transcript allows>"}]` to that item. The target is
    matched against the old decision's stored text by word overlap, so name it with the exact
    nouns the transcript uses for it (subject + object + qualifiers, e.g. "use Tutorials Dojo
    practice exam sets for week 9", not "Tutorials Dojo purchase plan") — never invent summary
    words the transcript does not contain, and never compress it below the words needed to pick
    it out. NEVER attach a supersedes link from topic overlap or similarity alone; the
    replacement must be stated explicitly, not guessed. Omit `links` entirely when no such
    replacement applies — do not emit an empty array.

17. QUOTE DISCIPLINE: a verbatim `quote` is a COPY-PASTE of ONE contiguous transcript span.
    Copy the characters exactly — punctuation, quotation marks, apostrophes, word for word.
    Never substitute quote characters, never add or drop a word, never reflow a list into
    prose. To skip content inside a quote you MUST mark the gap with `...` — an unmarked gap
    fails verification and the item loses verbatim status. Never stitch text from different
    speakers or turns into one quote, and never add scaffolding such as "User:" or "A:"
    labels inside a quote. If you cannot copy the exact characters of a contiguous span
    (or spans joined by `...`), use trust="inferred" with an empty quote instead —
    a correct inferred beats a downgraded verbatim.

Schema shape:
{
  "session_id": "<id>",
  "working_context": {
    "active_topic": {"text": "", "trust": "", "quote": "", "importance": 0},
    "open_questions": [{"text": "", "trust": "", "quote": "", "external_state": false, "importance": 0}],
    "recent_decisions": [{"text": "", "trust": "", "quote": "", "importance": 0, "links": [{"type": "", "target": ""}]}]
  },
  "epistemic_snapshot": {
    "strong_beliefs": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "uncertainties": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "contradictions_flagged": []
  },
  "worker_queue": []
}"""

# Adapted from research/experiments/track-a/prompts/01c-merge-checkpoints.md (armC),
# with two additions over the probe version: rule 9 (Q-STALE latest-state
# preference, findings/03) and external_state preservation (rule 3 + schema),
# minus emotional_valence (dropped in D-010, #101).
# Schema note: "worker_queue" is the deliberate Level-0 initiative placeholder —
# captured by the serializer but intentionally unrendered by the briefing (#101).
MERGE_SYS = """You are merging multiple partial cognitive-state checkpoints produced by chunk-by-chunk serialization of a long session transcript into one final checkpoint.

Output ONLY valid JSON conforming to the schema below. No prose before or after.

MERGE RULES — follow every one exactly:

1. UNION all items across all partial checkpoints. If an item appears in multiple chunks (possibly
   with slightly different wording due to chunked context), keep ONE canonical version — prefer the
   one with trust="verbatim" and a non-empty quote; otherwise prefer the fuller/more specific text.

2. DEDUPLICATE: two items are the same if they refer to the same real-world fact, decision, fix,
   or question. Minor wording differences do NOT make them distinct. Keep one. However, items that differ in SUBSTANCE — different decisions, or different uncertainties, even on the same topic — are NOT duplicates; keep them ALL. Only merge items that assert the same fact.

3. PRESERVE VERBATIM PINS (D-006): if any partial checkpoint has trust="verbatim" with a quote for
   an item, the merged output MUST also set trust="verbatim" and carry that exact quote. Never
   downgrade a verbatim item to inferred during merging. Likewise preserve any
   "external_state": true flag — it marks facts the next session must verify before trusting.

4. CHRONOLOGY: for recent_decisions and worker_queue, order items in the sequence they were made /
   appeared in the session (earliest chunk's items first). This is your best approximation; do NOT
   invent an order.

5. active_topic: pick from the LAST chunk's active_topic — it reflects where the session ended.
   If ambiguous, synthesize a brief inferred summary marked trust="inferred".

6. Do NOT invent items. If something appears only in one chunk, include it as-is. Do NOT discard
   items just because they appear in only one chunk.

7. contradictions_flagged: union across all chunks. If two chunks flag the same contradiction,
   deduplicate (keep one).

8. Output a single JSON object. No explanatory prose, no markdown fences — raw JSON only.

9. SUPERSESSION (staleness): when two partial checkpoints describe the SAME evolving fact at
    different points in the session — a number that was re-measured, a decision that was revised,
    a result that was corrected — keep ONLY the LATEST state (the one from the later chunk), and
    pin the LATEST quote. Do NOT keep the earlier value as a separate item, and do NOT pin an
    early quote to a fact whose final state changed. If the evolution itself matters, note it
    inside the surviving item's text ("X, revised from Y").

10. FINAL-STATE RECONCILIATION ACROSS CHUNKS: If a later partial's recent_decision or belief explicitly answers or supersedes an earlier partial's open_question on the same matter, DROP the open_question and keep the decision. Never the reverse — a later open_question does NOT un-settle an earlier decision unless the transcript explicitly reopened it.

11. IMPORTANCE: carry each item's integer `importance` (1-10) into the merged output. When
    deduplicating, keep the canonical item's score; if the duplicates' scores differ, keep the
    HIGHEST — under-weighting a load-bearing item costs more than over-weighting a minor one.

12. TRANSCRIPT LANGUAGE: keep every item's `text` in the same language as the transcript —
    merging must not translate items (a Spanish session stays Spanish). Never translate quotes;
    a `quote` is always the exact original wording. Schema keys and structure stay in English.

13. LINKS PRESERVATION: preserve every item's `links` array verbatim — never drop, alter, or
    invent a link entry. When deduplicating two items into one canonical item, the merged
    item's `links` is the union of both items' links (dedupe identical {type, target} pairs)
    so neither side's links are lost.

Schema shape:
{
  "session_id": "<id>",
  "working_context": {
    "active_topic": {"text": "", "trust": "", "quote": "", "importance": 0},
    "open_questions": [{"text": "", "trust": "", "quote": "", "external_state": false, "importance": 0}],
    "recent_decisions": [{"text": "", "trust": "", "quote": "", "importance": 0, "links": [{"type": "", "target": ""}]}]
  },
  "epistemic_snapshot": {
    "strong_beliefs": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "uncertainties": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "contradictions_flagged": []
  },
  "worker_queue": []
}"""

_TRUST_CLASSES = {"verbatim", "inferred"}


def chunk_transcript(text: str, chunk_lines: int, overlap_lines: int) -> list[str]:
    """Split rendered transcript text into overlapping line-based chunks.

    Same scheme as the D-007 probe's armC: fixed-size line windows stepping by
    (chunk_lines - overlap_lines), so consecutive chunks share overlap_lines of
    context and no decision falls in a blind spot at a boundary.
    """
    lines = text.splitlines()
    if len(lines) <= chunk_lines:
        return [text]
    chunks = []
    step = max(1, chunk_lines - overlap_lines)
    for i in range(0, len(lines), step):
        end = min(i + chunk_lines, len(lines))
        chunks.append("\n".join(lines[i:end]))
        if end >= len(lines):
            break
    return chunks


def _render_transcript(messages) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if isinstance(content, list):  # tool/multipart content -> flatten text parts
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def _valid_item(item) -> bool:
    if not isinstance(item, dict):
        return False
    if "text" not in item or "trust" not in item:
        return False
    # #134: presence != a usable value. A present-but-null (or non-str) text
    # passed this check, reached disk, then crashed briefing.render on the next
    # session. Reject at the boundary. Empty str stays valid — active_topic MAY
    # carry empty text (test_validate_allows_empty_active_topic_text).
    if not isinstance(item["text"], str):
        return False
    if item["trust"] not in _TRUST_CLASSES:
        return False
    if item["trust"] == "verbatim":
        quote = item.get("quote")
        # D-006: a verbatim claim without a real quote is an unpinned claim.
        if not isinstance(quote, str) or not quote.strip():
            return False
    anchor = item.get("anchored_to")
    if anchor is not None:
        if not isinstance(anchor, dict):
            return False
        if not all(
            isinstance(anchor.get(k), str) and anchor.get(k)
            for k in ("file", "symbol", "body_hash")
        ):
            return False
    return True


def iter_items(checkpoint):
    """Yield every schema item dict in a checkpoint: active_topic plus the five
    item lists, exactly the fields schema.ITEM_FIELDS declares (#146). Single
    source for cross-cutting per-item passes (#126) — store's first_seen
    stamping and sanitize_importance both walk exactly this set. Tolerant of
    absent keys and non-dict entries (torn/legacy checkpoints, and
    contradictions_flagged whose item shape varies)."""
    for field in schema.ITEM_FIELDS:
        block = checkpoint.get(field.section)
        if not isinstance(block, dict):
            continue
        if field.singleton:
            item = block.get(field.key)
            if isinstance(item, dict):
                yield item
            continue
        for item in block.get(field.key) or []:
            if isinstance(item, dict):
                yield item


def sanitize_importance(checkpoint) -> None:
    """Normalize LLM-emitted `importance` in place: ints clamp to 1..10, anything
    else (strings, floats, bools, None) is dropped. Malformed importance must
    NEVER fail a serialize — a new failure class here would recreate the #119
    heal-starvation incident for a purely advisory field."""
    for item in iter_items(checkpoint):
        if "importance" not in item:
            continue
        v = item["importance"]
        # bool is an int subclass — True must not become importance 1.
        if isinstance(v, int) and not isinstance(v, bool):
            item["importance"] = min(10, max(1, v))
        else:
            del item["importance"]


def validate(checkpoint) -> bool:
    """Light validation: required keys + trust-class integrity (D-006).

    Not full JSON-schema validation — just enough to refuse garbage before storing.

    Contract: active_topic MAY have empty text (sessions without a single clear
    topic); briefing.render() skips the empty section. Trust rules still apply.
    """
    if not isinstance(checkpoint, dict):
        return False
    if "session_id" not in checkpoint:
        return False
    wc = checkpoint.get("working_context")
    es = checkpoint.get("epistemic_snapshot")
    if not isinstance(wc, dict) or not isinstance(es, dict):
        return False
    if "active_topic" not in wc:
        return False
    for key in ("open_questions", "recent_decisions"):
        items = wc.get(key)
        if not isinstance(items, list):
            return False
        if not all(_valid_item(i) for i in items):
            return False
    if not _valid_item(wc["active_topic"]):
        return False
    for key in ("strong_beliefs", "uncertainties"):
        items = es.get(key, [])
        if not isinstance(items, list) or not all(_valid_item(i) for i in items):
            return False
    return True


# ---- #125: deterministic verbatim-quote verification ----
#
# The `verbatim` trust class promises the quote appears in the transcript, but
# nothing ever checked it — it was LLM self-report. These functions verify at
# serialize time, against the SAME rendered text the extractor read, using a
# fixed normalization stack ("tier f", measured in #125): the checker must be
# dumber than the thing it checks, so it is pure string ops, no LLM.

_MIN_FRAGMENT = 8   # an ellipsis fragment shorter than this after normalization
                    # is too generic to pin — dropped (a quote with none left is
                    # unverifiable, which fails conservatively).
_ELLIPSIS_RE = re.compile(r"\.\.\.|…")
_REDACTED_RE = re.compile(r"\[redacted:[^\]]*\]")
# Leading list markers ("- ", "* ", "1. ") anchored per line, stripped before
# whitespace folding collapses the newlines they depend on.
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*+]\s+|\d+\.\s+)", re.MULTILINE)
_MD_MARKER_RE = re.compile(r"[*`_~]")
_WS_RE = re.compile(r"\s+")
# Unicode punctuation folded to its ASCII look-alike before any stripping:
# extraction models routinely swap curly/straight quote glyphs and dash widths
# inside otherwise byte-faithful quotes (#208). U+2026 (…) is deliberately NOT
# folded — quote_matches splits the RAW quote on it as an elision marker before
# fragments reach this normalization.
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'",   # curly single quotes / apostrophe
    "“": '"', "”": '"',   # curly double quotes
    "–": "-", "—": "-",   # en dash / em dash
    "\u00a0": " ",             # non-breaking space (escaped: invisible in source)
})
# List markers that survive line-anchored stripping because they sit mid-string
# (a quote reflowing "- item" list lines into one line, #208). After whitespace
# folding, a marker token is one bounded by spaces (or string start) — bounding
# keeps hyphenated words ("re-verify") and decimals ("3.14") intact.
_INLINE_MARKER_RE = re.compile(r"(?:^|(?<= ))(?:[-*+]|\d+\.) ")


def _normalize_for_match(text: str) -> str:
    """Tier-f normalization shared by both sides of a quote match: fold unicode
    punctuation look-alikes to ASCII, strip markdown markers (list markers +
    emphasis chars) BEFORE folding whitespace so `**text**` equals `text`, then
    collapse whitespace, strip the space-bounded list markers the fold exposes
    mid-string, and casefold. Applied identically to quote and haystack, so
    symmetric folding/stripping never manufactures a match the raw text
    wouldn't support under the same fold."""
    text = text.translate(_PUNCT_FOLD)
    text = _LIST_MARKER_RE.sub("", text)
    text = _MD_MARKER_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    text = _INLINE_MARKER_RE.sub("", text)
    return text.casefold()


def quote_matches(quote, haystack) -> bool:
    """True when `quote` appears in `haystack` under tier-f normalization.

    Splits the quote on ellipsis into ordered fragments (an author eliding a
    span), drops fragments shorter than _MIN_FRAGMENT chars after normalization,
    and requires every surviving fragment to appear IN ORDER — each searched
    from the previous fragment's match end. A quote left with no usable fragment
    is unverifiable and returns False (conservative: never auto-pass). Redaction
    placeholders are stripped from fragments first, so a stored quote already
    carrying a `[redacted:...]` marker still matches."""
    if not isinstance(quote, str) or not isinstance(haystack, str):
        return False
    hay = _normalize_for_match(haystack)
    fragments = []
    for raw in _ELLIPSIS_RE.split(quote):
        frag = _normalize_for_match(_REDACTED_RE.sub("", raw))
        if len(frag) >= _MIN_FRAGMENT:
            fragments.append(frag)
    if not fragments:
        return False
    pos = 0
    for frag in fragments:
        idx = hay.find(frag, pos)
        if idx < 0:
            return False
        pos = idx + len(frag)
    return True


def verify_quotes(checkpoint, transcript_text: str) -> int:
    """Verify every verbatim item's quote against the rendered transcript, in
    place (#125). On a hit the item gets `quote_verified: true` AND a
    `last_verified` ISO-8601 UTC stamp (#215: the staleness-budget's freshest
    signal — a carried item's world-check age is measured from here). On a
    miss it is downgraded to trust="inferred" with `quote_verified: false` and
    the downgrade is logged (count + redacted item-text prefix — this runs
    pre-redaction, so the raw text must not reach a log sink; #141). Items
    already trust="inferred" are left untouched — no stamp, either field.
    Runs ONCE at serialize, PRE-redaction, so the quote is still the raw text
    (a quote whose secret redaction will later mask still verifies here
    against the raw rendered text). Returns the downgrade count.

    `last_verified` is checkpoint-append-only by design (#215): it is stamped
    ONLY here, at serialize time. No other code path may rewrite it — user
    resolve/reverify actions live in events.jsonl and are folded in at READ
    time (briefing.stale_carried), never written back onto the item.

    No injected `now` here (unlike briefing.build's now=None idiom): this
    function's existing two-positional-arg signature is called from exactly
    one site (serialize_strict, itself not now-aware), and datetime.now(...)
    inline matches store.append_event's own stamping idiom (store.py) rather
    than threading a new param through a call chain that has no other use
    for it."""
    downgraded = 0
    for item in iter_items(checkpoint):
        if item.get("trust") != "verbatim":
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        if quote_matches(quote, transcript_text):
            item["quote_verified"] = True
            item["last_verified"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        else:
            item["trust"] = "inferred"
            item["quote_verified"] = False
            downgraded += 1
            # Log-line-only scrub: item ids are not stamped until store-save,
            # so the text is the only diagnostic handle here. The item itself
            # stays raw (store redacts it; ids hash redacted text). Untruncated
            # (#194): this line is the only surviving record of the downgrade —
            # the CLI routes it to serialize.log, which holds full result lines.
            logged, _ = redact.redact_text(item.get("text") or "")
            log.warning("quote verification: downgraded verbatim->inferred: %s",
                        logged)
    if downgraded:
        log.info("quote verification: %d verbatim item(s) downgraded to inferred",
                 downgraded)
    return downgraded


def _call_and_parse(chat, system, user_content, deadline, what: str,
                    parse_retries: int = 1) -> dict:
    """One LLM call -> parsed JSON dict, with named failures.

    parse_retries re-calls when the response parses to nothing: reasoning
    models behind gateways intermittently return an empty or prose 200, which
    chat()'s transport retries (timeout/5xx/connection) never see. Chat
    failures are NOT retried here — chat() owns transport retries.

    Retries are never byte-identical: gateway response caches replay the same
    garbage for an identical request (H1 attempt 5 — LiteLLM returned the
    cached empty body in <1s). Each retry appends a per-attempt marker to the
    user content, making it a distinct request.
    """
    attempts = 1 + parse_retries
    for attempt in range(1, attempts + 1):
        content = user_content
        if attempt > 1:
            content += (
                f"\n\n(retry attempt {attempt} — the previous response was "
                f"unparseable; output ONLY the JSON object, no prose, no reasoning)"
            )
        try:
            raw = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                # No temperature pinned here: config.llm_temperature() governs
                # (default 0.0 for deterministic extraction; DAIMON_LLM_TEMPERATURE
                # overrides for upstreams that reject non-default values).
                deadline=deadline,
            )
        except Exception as exc:
            raise LLMCallError(f"LLM call failed on {what}: {type(exc).__name__}: {exc}") from exc

        def _can_retry():
            # A dead deadline makes a re-call pointless — fail now, named.
            return attempt < attempts and (
                deadline is None or deadline - time.monotonic() > 0
            )

        try:
            parsed = llm.extract_json(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            if _can_retry():
                # Never log `raw` — model output can echo request contents.
                log.warning("unparseable output on %s (attempt %d/%d), "
                            "retrying with cache-buster", what, attempt, attempts)
                continue
            raise OutputParseError(
                f"unparseable model output on {what} after {attempt} attempts: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            if _can_retry():
                log.warning("unparseable output on %s (attempt %d/%d), "
                            "retrying with cache-buster", what, attempt, attempts)
                continue
            raise OutputParseError(
                f"model output on {what} is not a JSON object after {attempt} attempts"
            )
        return parsed


def _merge_partials(chat, session_id: str, partials: list, deadline,
                    attempt_note: str = "") -> dict:
    """Hierarchically merge partial checkpoints into one, K partials at a time.

    Splits partials into CONSECUTIVE groups of config.merge_group_size() on each
    level; singletons pass through unchanged — no LLM call. Groups within a level
    run concurrently (same ThreadPoolExecutor pattern as chunk fan-out). Continues
    until a single partial remains, which is the merged result.

    `attempt_note` (#118) is appended to every merge request on a validation
    retry so no request is byte-identical to the failed pass — gateway response
    caches replay the same garbage for an identical request.
    """
    K = config.merge_group_size()
    level = 0
    while len(partials) > 1:
        level += 1
        groups = [partials[i:i + K] for i in range(0, len(partials), K)]
        n_groups = len(groups)
        log.info("merge level %d: %d group(s)", level, n_groups)

        def _one_group(item, _level=level, _n_groups=n_groups):
            g, group = item
            if len(group) == 1:
                # Singleton — pass through without an LLM call.
                return group[0]
            t0 = time.monotonic()
            merged = _call_and_parse(
                chat, MERGE_SYS,
                f"session_id: {session_id}\n\n"
                f"PARTIAL CHECKPOINTS (JSON array, one per chunk, in chronological order):\n"
                f"{json.dumps(group, ensure_ascii=False)}"
                f"{attempt_note}",
                deadline, f"merge level {_level}, group {g + 1} of {_n_groups}",
            )
            log.info("merge level %d, group %d/%d done in %.0fs",
                     _level, g + 1, _n_groups, time.monotonic() - t0)
            return merged

        workers = min(config.chunk_concurrency(), len(groups))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # pool.map preserves input order, keeping chronological sequence intact.
            partials = list(pool.map(_one_group, enumerate(groups)))
    return partials[0]


def serialize_strict(session_id: str, messages, chat=None, deadline=None) -> dict:
    """Transcript -> validated checkpoint, or a named SerializeError.

    `chat` is an injectable callable (messages, **kwargs) -> str; defaults to the
    real LLM client. `deadline` (time.monotonic() seconds) is the TOTAL remaining
    budget across ALL LLM calls (every chunk + merge), forwarded to the client.

    Rendered transcripts over DAIMON_CHUNK_LINES go chunked (armC): per-chunk
    D-007 serialize -> 01c merge -> validate. Shorter ones stay single-pass.
    """
    if chat is None:
        chat = llm.chat
    n = len(messages) if messages else 0
    if n < config.min_messages():
        raise TooShortError(
            f"transcript too short ({n} < {config.min_messages()} messages)"
        )
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise LLMCallError("deadline exhausted before the first LLM call")

    transcript_text = _render_transcript(messages)
    chunks = chunk_transcript(transcript_text, config.chunk_lines(), config.chunk_overlap())

    # Validation-failure retry note (#118): one resample with a non-identical
    # request. Occasional invalid output (the live case: quote inlined into a
    # verbatim item's text, `quote` field omitted) is ordinary model flakiness,
    # but gateway response caches replay the SAME bad body for a byte-identical
    # retry — so heal could never recover. Same lesson _call_and_parse already
    # encodes for parse failures.
    _RETRY_NOTE = (
        "\n\nattempt 2: the previous output failed schema validation — "
        'every trust="verbatim" item MUST carry its exact transcript quote in '
        "its `quote` field (never inlined into `text`). The quote must be "
        "copy-pasted exactly from the transcript, elisions marked with `...`. "
        "Re-emit the full corrected JSON."
    )
    partials: list | None = None

    def _produce(note: str) -> dict:
        nonlocal partials
        if len(chunks) == 1:
            log.info("single-pass serialize: %d lines", len(transcript_text.splitlines()))
            return _call_and_parse(
                chat, SERIALIZE_SYS,
                f"session_id: {session_id}\n\nTRANSCRIPT:\n{transcript_text}{note}",
                deadline, "transcript",
            )
        if partials is None:
            log.info("chunked serialize: %d chunks from %d lines",
                     len(chunks), len(transcript_text.splitlines()))

            # Chunks are independent — run them concurrently. Gateway calls are
            # generation-bound (~minutes each); sequential fan-out made a long
            # session take chunk_count * minutes of wall-clock.
            def _one_chunk(item):
                i, chunk_text = item
                t0 = time.monotonic()
                partial = _call_and_parse(
                    chat, SERIALIZE_SYS,
                    f"session_id: {session_id}\n\n"
                    f"TRANSCRIPT (chunk {i + 1} of {len(chunks)}):\n{chunk_text}",
                    deadline, f"chunk {i + 1} of {len(chunks)}",
                )
                log.info("chunk %d/%d done in %.0fs",
                         i + 1, len(chunks), time.monotonic() - t0)
                return partial

            workers = min(config.chunk_concurrency(), len(chunks))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # executor.map preserves input order, so partials stay chronological.
                partials = list(pool.map(_one_chunk, enumerate(chunks)))
        # Retry re-runs ONLY the merge (the final sampling that failed) — the
        # chunk partials are kept; they are the expensive calls.
        return _merge_partials(chat, session_id, list(partials), deadline,
                               attempt_note=note)

    checkpoint = _produce("")
    checkpoint["session_id"] = session_id
    if not validate(checkpoint):
        log.info("checkpoint failed validation — one resample with attempt nonce (#118)")
        checkpoint = _produce(_RETRY_NOTE)
        checkpoint["session_id"] = session_id
    if not validate(checkpoint):
        raise SchemaValidationError(
            "checkpoint failed schema/trust validation (missing keys, bad trust "
            "class, or verbatim item without a quote)"
        )
    sanitize_importance(checkpoint)
    # #125: verify verbatim quotes against the SAME rendered text the extractor
    # read, PRE-redaction (redaction runs later in write_checkpoint and would
    # otherwise mass-downgrade legitimate quotes it had masked). Verify once,
    # stamp the verdict — the briefing never re-greps.
    verify_quotes(checkpoint, transcript_text)
    return checkpoint


def serialize(session_id: str, messages, chat=None, deadline=None) -> dict | None:
    """Never-raise wrapper around serialize_strict() — the hermes hook contract.

    Returns None on any named failure (and on unexpected exceptions).
    """
    try:
        return serialize_strict(session_id, messages, chat=chat, deadline=deadline)
    except SerializeError:
        return None
    except Exception:
        return None
