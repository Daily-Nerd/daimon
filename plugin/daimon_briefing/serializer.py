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

import hashlib
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, configure, llm, redact, schema

# No handlers/basicConfig here — the library stays silent unless the caller
# configures logging. Multi-hour serialize runs need this heartbeat to be killable.
log = logging.getLogger(__name__)


# Serialize-prompt version. Bumped D-008 -> D-010 (#101: emotional_valence
# dropped from the schema; D-009 is taken by the host-adapter decision).
# D-012 -> D-013 (#208: quote copy-paste discipline rule).
# D-013 -> D-014 (#287: external-artifact identifier rule — "issue #5"
# without a repo is half a pointer; capture the most specific identifier
# the transcript states, never invent one).
# D-014 -> D-015 (#358: verbatim items bind to source transcript message ids;
# the bump also rotates the #48 chunk-cache key so pre-#358 cached
# extractions, which carry no ids, can never satisfy a post-#358 request).
# D-015 -> D-016 (#359: outcome claims ground in tool-result signals — rule
# 20 asks for signal citations, and the bump rotates the #48 chunk-cache key
# so pre-#359 cached extractions, whose chunks rendered no tool rows, can
# never satisfy a post-#359 request).
# Checkpoints are only comparable across runs sharing this version (scar
# landmine #4); pre-bump checkpoints firing the #93 format_version mismatch
# warning is desired, not a bug.
PROMPT_VERSION = "D-016"


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

18. EXTERNAL ARTIFACT IDENTIFIERS: when an item references an external artifact — a repo,
    issue, PR, package, ticket, deploy target — include that artifact's MOST SPECIFIC identifier
    stated anywhere in the transcript in the item's `text`: repo slug (owner/name), issue/PR as
    owner/name#123 or its full URL, package name with version, ticket key. "File issue #5
    upstream" is half a pointer — a future session cannot resolve which repo it means; "file
    issue #5 in acme/widget-lib" is whole. The identifier may come from a DIFFERENT part of the
    transcript than the sentence you are extracting (the quote rules still apply to `quote`;
    this rule is about `text`). Never invent an identifier the transcript does not contain;
    if only a vague name was ever stated, keep the vague name.

19. SOURCE MESSAGE IDS: transcript messages may be prefixed with a bracketed marker such as
    [m12] identifying that message. For every trust="verbatim" item, add
    "source_message_ids": ["m12"] — the marker id(s) of the exact message(s) the `quote` was
    copied from, normally exactly one. Copy the id from the marker exactly, without the
    brackets. Item shapes gain this one optional key; inferred items carry it only in the
    rule-20 outcome-signal case. If the transcript shows no [mN] markers, or you cannot tell
    exactly which message the quote came from, omit the field entirely — never guess or
    invent an id.

20. OUTCOME GROUNDING: messages rendered as "tool:" (or "tool (error):") are TOOL RESULTS —
    command output, exit status, test runs — evidence, not conversation. When an item's claim
    asserts a concrete OUTCOME (something succeeded or failed, was merged, deployed,
    released, tests passed or went green, a build completed), and a tool-result message in
    this transcript actually SHOWS that outcome happening, add that message's [mN] marker id
    to the item's "source_message_ids" — alongside the quote's own marker for verbatim items
    (keep both). This is the one case where an inferred item carries "source_message_ids".
    Cite only a tool-result message that genuinely evidences the outcome; never copy tool
    output into `text` or `quote` because of this rule. If no tool-result message evidences
    the outcome, add nothing — the absence is itself a signal.

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

# ---- #317: scene traces (opt-in experiment, DAIMON_SCENE_TRACES) ----
#
# Appended to BOTH system prompts when the flag is on; with the flag off the
# prompts are byte-identical to the constants above (pinned by test — the
# experiment must cost nothing until the LongMemEval harness says it earns
# its bytes). Length cap bounds checkpoint growth; sanitize_scene enforces it
# on whatever the model actually emits, flag or no flag.
_SCENE_MAX_CHARS = 500

_SCENE_SERIALIZE_ADDENDUM = """

SCENE TRACES (optional field): every item MAY additionally carry a "scene" field —
one or two sentences of episodic context: when in the session the item arose, what
triggered it, and what it replaced or corrected. Narrative only, drawn from the
transcript — never introduce a fact, name, or number that the transcript does not
contain. "scene" never affects trust: it is always inferred narrative, and the
rules for `text` and `quote` are unchanged. Item shapes gain one key:
{"text": "", "trust": "", "quote": "", "scene": "", "importance": 0}"""

_SCENE_MERGE_ADDENDUM = """

SCENE TRACES: items may carry an optional "scene" field (episodic context). When
merging duplicates, keep the fuller "scene"; never invent one for an item that has
none, and never let a "scene" override the text/quote/trust rules above."""


def _serialize_sys() -> str:
    if config.scene_traces_enabled():
        return SERIALIZE_SYS + _SCENE_SERIALIZE_ADDENDUM
    return SERIALIZE_SYS


def _merge_sys() -> str:
    if config.scene_traces_enabled():
        return MERGE_SYS + _SCENE_MERGE_ADDENDUM
    return MERGE_SYS


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

14. SOURCE MESSAGE IDS: an item's optional "source_message_ids" array travels WITH its quote:
    the canonical item keeps the ids of the version whose quote it keeps. Never invent ids,
    never alter them, and never move them onto an item with a different quote. Preserve them
    like `links` — dropping them loses provenance. Ids may ALSO point at tool-result
    messages that evidence an outcome claim (these can appear on inferred items too):
    preserve those on the canonical item exactly the same way, even when it has no quote.

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


def _message_text(m) -> str:
    content = m.get("content", "")
    if isinstance(content, list):  # tool/multipart content -> flatten text parts
        content = " ".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    return content


def _message_id(m) -> str | None:
    """The host-stable per-message id transcript.py attached (#358), or None."""
    if not isinstance(m, dict):
        return None
    mid = m.get("id")
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    return None


def _render_transcript(messages) -> str:
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role", "unknown")
        # #359: a failed tool result renders its error status inline — the
        # extractor must see WHICH way the signal points, not just that one
        # exists. Only flagged tool rows (transcript.py's Claude Code branch)
        # qualify; a markdown "tool:" role row renders exactly as before.
        if m.get("tool_result") and m.get("tool_error"):
            role = f"{role} (error)"
        content = _message_text(m)
        # #358: a bracketed [mN] marker names an identified message so the
        # extractor can cite where each verbatim quote came from. Id-less
        # messages (hosts without stable ids) render byte-identical to the
        # pre-#358 format — no marker, no behavior change.
        if _message_id(m) is not None:
            lines.append(f"[m{i + 1}] {role}: {content}")
        else:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def message_id_map(messages) -> dict[str, str]:
    """Rendered marker ("m3") -> host message id, for messages carrying one.

    Positional 1-based numbering over the FULL message list, matching the
    markers _render_transcript emits — stable under transcript growth for the
    unchanged prefix, which is what lets #48 cached chunk extractions keep
    citing valid markers."""
    out: dict[str, str] = {}
    for i, m in enumerate(messages or []):
        mid = _message_id(m)
        if mid is not None:
            out[f"m{i + 1}"] = mid
    return out


def message_texts_by_id(messages) -> dict[str, str]:
    """Host message id -> flattened message text, for id-scoped quote checks."""
    out: dict[str, str] = {}
    for m in messages or []:
        mid = _message_id(m)
        if mid is not None:
            out[mid] = _message_text(m)
    return out


def signal_message_ids(messages) -> set[str]:
    """Host ids of signal-bearing messages: tool results (#359).

    Keyed on the `tool_result` flag transcript.py sets, never on the role
    string — a markdown transcript's "tool:" role row has no tool payload
    behind it and must not count as evidence. Empty for hosts that surface
    no tool rows (Windsurf, Codex, hermes, markdown): grounding degrades to
    a no-op there."""
    out: set[str] = set()
    for m in messages or []:
        if not (isinstance(m, dict) and m.get("tool_result")):
            continue
        mid = _message_id(m)
        if mid is not None:
            out.add(mid)
    return out


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


def sanitize_scene(checkpoint) -> None:
    """Normalize LLM-emitted `scene` (#317) in place: strings are stripped and
    capped at _SCENE_MAX_CHARS; anything else (lists, dicts, numbers, None,
    empty/whitespace) is dropped. Same philosophy as sanitize_importance: a
    malformed advisory field must NEVER fail a serialize — and it runs flag or
    no flag, because a model can hallucinate the key without being asked."""
    for item in iter_items(checkpoint):
        if "scene" not in item:
            continue
        v = item["scene"]
        if isinstance(v, str) and v.strip():
            item["scene"] = v.strip()[:_SCENE_MAX_CHARS]
        else:
            del item["scene"]


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


# ---- #358: verbatim items bind to transcript message ids ----
#
# Capture-time binding: the extractor cites, per verbatim item, the [mN]
# marker of the message its quote came from (rule 19). The parse boundary
# below translates markers to host ids and drops anything the actual
# transcript cannot vouch for — the same code-owned-key discipline as
# #292/#295, one level down: the model proposes, only ids the code resolved
# survive. Ids ride inside the item payload, so receipts cover them with no
# receipt-machinery change.

SOURCE_IDS_KEY = "source_message_ids"


def sanitize_source_ids(checkpoint, id_map, signal_ids=frozenset()) -> None:
    """Validate model-emitted source message ids in place (#358).

    `id_map` maps rendered markers ("m3") to host message ids
    (message_id_map). Per item: a bare string becomes a one-entry list;
    marker entries (brackets tolerated) translate to their host id; entries
    already equal to a known host id pass through (merged/cached partials);
    everything else — unknown ids, non-strings, ids on inferred or
    quote-less items — is dropped, and the key is removed when nothing valid
    remains. Same never-fatal philosophy as sanitize_importance: an advisory
    field must never fail a serialize. Callers with no transcript to
    validate against (cli's #23 write-checkpoint path) pass {} — every
    claimed binding drops.

    #359 widens "bindable" by exactly one case: an id resolving into
    `signal_ids` (host ids of tool-result messages, signal_message_ids) is a
    SIGNAL pointer — an outcome claim's evidence — and is kept on ANY item,
    inferred and quote-less included. Non-signal ids on inferred items still
    drop: the quote-binding rule is unchanged."""
    id_map = id_map or {}
    signal_ids = set(signal_ids or ())
    known_hosts = set(id_map.values())
    for item in iter_items(checkpoint):
        if SOURCE_IDS_KEY not in item:
            continue
        raw = item[SOURCE_IDS_KEY]
        if isinstance(raw, str):
            raw = [raw]
        out: list[str] = []
        quote = item.get("quote")
        bindable = (item.get("trust") == "verbatim"
                    and isinstance(quote, str) and quote.strip())
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, str):
                    continue
                marker = entry.strip().strip("[]")
                host = id_map.get(marker)
                if host is None and entry.strip() in known_hosts:
                    host = entry.strip()
                if host is None or host in out:
                    continue
                if bindable or host in signal_ids:
                    out.append(host)
        if out:
            item[SOURCE_IDS_KEY] = out
        else:
            del item[SOURCE_IDS_KEY]


def scoped_haystack(item, texts_by_id, exclude=frozenset()) -> str | None:
    """The id-scoped haystack for an item's bound message(s), or None.

    None means "no usable binding" — ids absent, or ANY cited id missing from
    `texts_by_id` (old checkpoints, moved/truncated transcripts, carried
    items from another session) — and the caller falls back to the
    whole-transcript scan, exactly today's behavior. An unresolvable id is
    not a disproven one.

    `exclude` (#359): ids to leave OUT of the haystack — verify_quotes passes
    the session's signal ids, because a tool-result pointer asserts "this
    evidences the outcome", not "the quote lives here". An item whose cited
    ids are ALL excluded has no quote-source claim at all -> None."""
    ids = item.get(SOURCE_IDS_KEY) if isinstance(item, dict) else None
    if not (isinstance(ids, list) and ids and texts_by_id):
        return None
    ids = [i for i in ids if not (isinstance(i, str) and i in exclude)]
    if not ids:
        return None
    parts = []
    for mid in ids:
        text = texts_by_id.get(mid) if isinstance(mid, str) else None
        if text is None:
            return None
        parts.append(text)
    return "\n\n".join(parts)


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


def verify_quotes(checkpoint, transcript_text: str, messages=None) -> int:
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

    #358: when `messages` is given, an item bound to source message id(s) is
    checked against JUST those messages first — resolve id, compare bytes. A
    scoped hit keeps the binding; a scoped MISS falls back to the
    whole-transcript scan so the verdict is byte-identical to today's, but a
    real-quote-in-the-wrong-message binding (scar #10's item-identity
    ambiguity, disproven direction) is dropped rather than stored as false
    provenance. Unresolvable ids (not in `messages`) are not disproven —
    fallback rules, binding left alone. Without `messages` (legacy two-arg
    callers) bindings are neither used nor touched.

    `last_verified` is checkpoint-append-only by design (#215): it is stamped
    ONLY here, at serialize time. No other code path may rewrite it — user
    resolve/reverify actions live in events.jsonl and are folded in at READ
    time (briefing.stale_carried), never written back onto the item.

    No injected `now` here (unlike briefing.build's now=None idiom): this
    function's signature is called from exactly one production site
    (serialize_strict, itself not now-aware), and datetime.now(...) inline
    matches store.append_event's own stamping idiom (store.py) rather than
    threading a new param through a call chain that has no other use for
    it."""
    texts_by_id = message_texts_by_id(messages) if messages else {}
    # #359: signal pointers (tool-result ids) are outcome evidence, not
    # quote-source claims — they never scope the quote check, and a scoped
    # MISS must not execute them for the quote-id's crime.
    signals = signal_message_ids(messages) if messages else set()
    downgraded = 0
    for item in iter_items(checkpoint):
        if item.get("trust") != "verbatim":
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        scoped = scoped_haystack(item, texts_by_id, exclude=signals)
        if scoped is not None and quote_matches(quote, scoped):
            item["quote_verified"] = True
            item["last_verified"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        elif quote_matches(quote, transcript_text):
            if scoped is not None:
                # Resolved AND mismatched: the quote is real but not in its
                # cited message — drop the disproven QUOTE binding (signal
                # pointers survive, #359), keep the pre-#358 verdict.
                kept = [i for i in item.get(SOURCE_IDS_KEY) or []
                        if isinstance(i, str) and i in signals]
                if kept:
                    item[SOURCE_IDS_KEY] = kept
                else:
                    item.pop(SOURCE_IDS_KEY, None)
                log.warning("quote verification: quote not found in its cited "
                            "message(s) — binding dropped, verified via "
                            "whole-transcript scan (#358)")
            item["quote_verified"] = True
            item["last_verified"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        else:
            item["trust"] = "inferred"
            item["quote_verified"] = False
            # A downgraded quote is not evidence; a binding for it is noise.
            item.pop(SOURCE_IDS_KEY, None)
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


# ---- #359: outcome claims ground in tool-result signals ----
#
# The hard trust-class gap (#185/#194 lineage): the model concludes X, X is
# false, the transcript faithfully records the model saying X — verbatim
# matching certifies TRANSCRIPTION, not truth. For claims that assert an
# OUTCOME (succeeded/failed/merged/deployed/tests green), the transcript
# usually holds a concrete signal — a tool result, an exit status — and
# rule 20 asks the extractor to cite it. `grounded` is the code-derived
# verdict over the validated pointers: True = cites a real signal message,
# False = outcome-shaped claim in a signal-bearing session with no citation
# (stored inferred — an unwitnessed outcome is a report, not a fact).
# Deliberately NO new trust class and NO new rendered tag: briefing trust
# literals are pinned (skill-distribution scar), so this ships as an
# additive advisory field the briefing can surface later. Items only ever
# carry the POINTER (message id) — never the signal payload, so redaction
# semantics are untouched.

GROUNDED_KEY = "grounded"

# Conservative, English-only outcome lexicon: past-tense/state assertions
# about completion. Non-English claims simply never match — grounding stays
# absent there, which is the honest no-op (never downgrade on a guess).
_OUTCOME_RE = re.compile(
    r"(?:\b(?:succeeded|successfully)\b"
    r"|\btests?\s+(?:all\s+|are\s+|now\s+)*(?:pass(?:ed|ing)?|green)\b"
    r"|\b(?:build|suite|ci|pipeline|deploy(?:ment)?)\s+"
    r"(?:is\s+|now\s+)*(?:pass(?:ed|ing)?|green|succeeded|failed|completed)\b"
    r"|\b(?:merged|deployed|released|published|shipped|landed)\b"
    r"|\ball\s+(?:\d+\s+)?tests?\s+pass(?:ed)?\b)",
    re.IGNORECASE)
# Hedge/future/plan markers: "will be merged" is a plan, "whether the deploy
# succeeded" is a question — neither ASSERTS the outcome. When one of these
# is present the claim is not an outcome assertion and stays untouched
# (when in doubt, keep today's behavior).
_HEDGE_RE = re.compile(
    r"\b(?:will|would|should|shall|going\s+to|to\s+be|not\s+yet|pending|"
    r"plan(?:ned|s|ning)?|todo|must|needs?\s+to|about\s+to|once|when|"
    r"if|whether|did|does|can|could|may|might)\b",
    re.IGNORECASE)


def _asserts_outcome(text: str) -> bool:
    """True when `text` ASSERTS a completed outcome (narrow, English-only)."""
    if not isinstance(text, str):
        return False
    return bool(_OUTCOME_RE.search(text)) and not _HEDGE_RE.search(text)


def ground_outcomes(checkpoint, signal_ids) -> int:
    """Derive the code-owned `grounded` verdict in place (#359). Returns the
    number of verbatim outcome claims downgraded to inferred.

    Runs AFTER sanitize_source_ids (only code-validated pointers exist) and
    AFTER verify_quotes (which may drop disproven bindings — grounding must
    judge the surviving set, or a dropped pointer could leave a stale True).

    Per item, in order:
    - the model never gets a vote: any model-emitted `grounded` is stripped
      first (#292 discipline), then re-derived or left absent;
    - a validated pointer into `signal_ids` -> grounded: true (the claim
      cites a concrete tool-result signal in this session);
    - otherwise, IF this session surfaced signals at all AND the item is
      trust="verbatim" AND its `text` asserts an outcome -> trust becomes
      "inferred", grounded: false. The quote (and its quote_verified stamp)
      stays: transcription remains honestly attested — it is the OUTCOME
      that is unwitnessed;
    - everything else is untouched. Signal-free sessions (Windsurf, Codex,
      hermes, markdown — no parseable tool results) never downgrade:
      grounding is impossible there, and absence of evidence about the HOST
      is not evidence against the claim.

    Same never-fatal philosophy as sanitize_importance: pure dict walking,
    an advisory field must never fail a serialize."""
    signal_ids = set(signal_ids or ())
    downgraded = 0
    for item in iter_items(checkpoint):
        item.pop(GROUNDED_KEY, None)
        ids = item.get(SOURCE_IDS_KEY)
        if (isinstance(ids, list)
                and any(isinstance(i, str) and i in signal_ids for i in ids)):
            item[GROUNDED_KEY] = True
            continue
        if not signal_ids:
            continue
        if item.get("trust") != "verbatim":
            continue
        if not _asserts_outcome(item.get("text") or ""):
            continue
        item["trust"] = "inferred"
        item[GROUNDED_KEY] = False
        downgraded += 1
        # Same log-line-only scrub as verify_quotes: runs pre-redaction.
        logged, _ = redact.redact_text(item.get("text") or "")
        log.warning("outcome grounding: unwitnessed outcome claim downgraded "
                    "verbatim->inferred (no signal cited): %s", logged)
    if downgraded:
        log.info("outcome grounding: %d outcome claim(s) downgraded to inferred",
                 downgraded)
    return downgraded


def _call_and_parse(chat, system, user_content, deadline, what: str,
                    parse_retries: int = 1) -> dict:
    """One LLM call -> parsed JSON dict, with named failures.

    parse_retries re-calls when the response parses to nothing: reasoning
    models behind gateways intermittently return an empty or prose 200, which
    chat()'s transport retries (timeout/5xx/connection) never see. Chat
    failures are NOT retried here — chat() owns transport retries. The one
    exception is llm.EmptyOutputError (#225): the command backend's rc=0 +
    empty-stdout case is functionally the same "the backend said nothing" as
    an empty HTTP 200 body, so it gets the same cache-buster retry treatment
    instead of failing on attempt 1. Every OTHER ChatError (transport
    failures) keeps failing immediately here — those are chat()'s own retry
    domain.

    Retries are never byte-identical — not within a run, and not ACROSS runs:
    gateway response caches replay the same garbage for an identical request
    (H1 attempt 5 — LiteLLM returned the cached empty body in <1s). The retry
    marker carries the attempt number AND a per-invocation nonce, because
    attempt numbers restart every invocation: without the nonce, a re-heal's
    retries were byte-identical to the failed run's and ate the same pinned
    bad response in 0s, forever (#312).

    Attempt 1 stays pristine ON PURPOSE — no marker, no nonce. A gateway
    replaying a COMPLETED good response for the clean request is a feature:
    it is what let a deadline-killed chunked serialize recover its paid-for
    chunks and merge in 0s on the next heal (#314's partial-loss case). If
    attempt 1 replays pinned garbage instead, it costs milliseconds and the
    nonce'd attempt 2 goes to a real model.
    """
    attempts = 1 + parse_retries
    run_nonce = uuid.uuid4().hex[:12]
    for attempt in range(1, attempts + 1):
        content = user_content
        if attempt > 1:
            content += (
                f"\n\n(retry attempt {attempt} [{run_nonce}] — the previous "
                f"response was unparseable; output ONLY the JSON object, "
                f"no prose, no reasoning)"
            )

        def _can_retry(_attempt=attempt):
            # A dead deadline makes a re-call pointless — fail now, named.
            return _attempt < attempts and (
                deadline is None or deadline - time.monotonic() > 0
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
        except llm.EmptyOutputError as exc:
            if _can_retry():
                log.warning("empty output on %s (attempt %d/%d), "
                            "retrying with cache-buster", what, attempt, attempts)
                continue
            raise LLMCallError(f"LLM call failed on {what}: {type(exc).__name__}: {exc}") from exc
        except Exception as exc:
            raise LLMCallError(f"LLM call failed on {what}: {type(exc).__name__}: {exc}") from exc

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


def _plan_waves(n_chunks: int, workers: int, k: int) -> int:
    """How many sequential LLM 'waves' a chunked serialize needs (#314).

    Chunks run concurrently, so n_chunks costs ceil(n/workers) waves, not n.
    Each merge level's non-singleton groups run concurrently too; singleton
    groups pass through free. This is the multiplier for the total deadline:
    DAIMON_TIMEOUT was field-derived as the floor for ONE slow call (#284),
    so a plan of W waves gets W times that budget — otherwise the merge is
    structurally guaranteed to start starved on slow gateways.

    Per-call socket timeouts stay capped at the base budget (llm.py caps each
    attempt to min(timeout, remaining)), so scaling the total never pushes a
    single request past gateway kill ceilings (scar #17: ~815s)."""
    if n_chunks <= 1:
        return 1
    w = max(1, workers)
    waves = (n_chunks + w - 1) // w
    m = n_chunks
    while m > 1:
        n_groups = (m + k - 1) // k
        # Only the last group can be a singleton (when m % k == 1); it merges
        # without an LLM call.
        call_groups = n_groups - (1 if m % k == 1 else 0)
        if call_groups > 0:
            waves += (call_groups + w - 1) // w
        m = n_groups
    return waves


# ---- #48: content-addressed chunk-extraction cache (generalizes #314) --------
# Keyed on chunk TEXT plus every config dimension that shapes extraction
# (backend, model, temperature, prompt version, and a hash of the actual
# serialize system prompt — so an un-versioned prompt edit, like the #317
# scene appendix, invalidates cleanly). session_id is deliberately NOT in the
# key: prefix chunks of a grown or resume-forked transcript are byte-identical
# and their paid-for outputs transfer (#48). The prompt embeds a positional
# "chunk i of n" label that the key ignores — presentation metadata, not
# extraction semantics.
#
# Entries persist across successful serializes (that IS the feature) and are
# reaped by age. Cached output is PRE-redaction — forced by #125: quotes are
# verified against the pre-redaction transcript, and caching redacted output
# would mass-downgrade legitimate verbatim items on every hit. The rotation
# window (config.chunk_cache_days, default 3) is therefore a privacy bound;
# files are written 0600. Same sensitivity and root as checkpoints.
# Everything here is best-effort: a broken cache must never break a
# serialize — worst case is re-paying the chunk call.


def _chunk_cache_dir():
    return config.checkpoint_dir() / ".chunk-cache"


def _chunk_cache_key(chunk_text: str) -> str:
    try:
        backend = configure.resolved_backend()
    except Exception:
        backend = "unknown"
    sys_hash = hashlib.sha256(_serialize_sys().encode("utf-8")).hexdigest()[:16]
    stamp = (f"v1\x00{backend}\x00{config.llm_model() or ''}"
             f"\x00{config.llm_temperature()}\x00{PROMPT_VERSION}"
             f"\x00{sys_hash}\x00")
    return hashlib.sha256(
        stamp.encode("utf-8") + chunk_text.encode("utf-8")).hexdigest()[:32]


def _load_chunk_cache(key: str):
    # DAIMON_LLM_NO_CACHE is the gateway-cache bypass, but a user reaching for
    # it means "no replayed LLM output" — honor the intent here too (reads
    # only; writing a fresh result is still fine).
    if not config.chunk_cache_enabled() or config.llm_no_cache():
        return None
    try:
        obj = json.loads(
            (_chunk_cache_dir() / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _save_chunk_cache(key: str, partial: dict) -> None:
    if not config.chunk_cache_enabled():
        return
    if llm.fallback_used():
        # #28/#343 lesson: once the weaker fallback backend has fired in this
        # process, nothing from this run may be cached under the primary
        # backend's key — that is exactly how caches get poisoned.
        return
    d = _chunk_cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        max_age = config.chunk_cache_days() * 24 * 3600
        now = time.time()
        for stale in d.glob("*.json"):  # reap by age on every write
            try:
                if now - stale.stat().st_mtime > max_age:
                    stale.unlink()
            except OSError:
                continue
        tmp = d / f".{key}.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_text(json.dumps(partial, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(d / f"{key}.json")
    except OSError:
        pass


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
                chat, _merge_sys(),
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


# #292: keys the CODE asserts about a checkpoint's origin — never data a
# MODEL-authored dict should get a vote on. The serialize prompt never asks
# for any of these, but a transcript that happens to discuss daimon's own
# schema (the field report behind this: a transcript quoting daimon's own
# format-drift warning banner) can make the model emit one anyway — and the
# #23 introspection path (cli's `write-checkpoint`) has the model author a
# schema-shaped dict directly. Nothing else on the write path catches it:
# `_valid_item` only validates item fields, and store.write_checkpoint's
# setdefault stamps defer to whatever key is already present — which is
# indistinguishable from a legitimate re-write of an already-stamped
# checkpoint (#93/#123). `session_id` needs no entry here: it's already
# reassigned by direct `=` right after every _produce() call below, which
# already stomps a model-supplied value the same way.
_CODE_OWNED_KEYS = (
    "format_version", "created", "author",
    "transcript_hash", "project_slug", "git_branch", "receipts",
)


def strip_code_owned_keys(checkpoint: dict) -> None:
    """Discard any code-owned key a model emitted in its own output.

    Public: called both here (after every fresh _produce() parse) and by
    cli's `_cmd_write_checkpoint`, the other place a model authors a
    checkpoint dict directly. Never call this on a checkpoint that came off
    disk (e.g. anchor --attach's read-mutate-rewrite) — that would erase its
    real stamps and let store.write_checkpoint's setdefault silently
    re-date `created` and jump `format_version` to whatever's current. Only
    dicts a model just authored are candidates for stripping.

    Fail-safe, not fail-fast: a model that names one of these fields is not
    an error worth failing an otherwise-good write over — just a value that
    must never be load-bearing. Runs before session_id is assigned (serialize
    path) or before store.write_checkpoint ever sees it (introspection path),
    so store's later setdefault stamps land on the code's own values (cli's
    own `created`/`transcript_hash` assignments, store's format_version/
    author/project_slug/git_branch/receipts) — never a model-supplied one.
    """
    for key in _CODE_OWNED_KEYS:
        if key in checkpoint:
            log.info("serialize: discarding model-supplied code-owned key %r", key)
            del checkpoint[key]


def _stamp_llm_provenance(checkpoint: dict) -> None:
    """Stamp which backend/model actually produced this checkpoint (#230).

    `llm_backend` is resolved via configure.resolved_backend() — the EXACT
    function llm.chat()'s `auto` branch mirrors (configure.py's own docstring
    promise) — so this can never disagree with what the serialize actually
    ran on. Not re-derived here with separate logic that could drift.

    `llm_model` is stamped only when config actually knows a model string
    (DAIMON_LLM_MODEL/LITELLM_MODEL — an HTTP backend's model, or whatever a
    command/claude-cli setup has explicitly configured). The claude-cli
    preset's hardcoded `--model haiku` isn't config-known, so a bare
    command/claude-cli backend with no explicit model setting leaves
    `llm_model` ABSENT rather than guessing one.

    Direct ASSIGNMENT, not setdefault — deliberate contrast with
    git_branch's setdefault in store.py (#222):
    (a) a heal/re-serialize is a fresh LLM run, and the backend that ran
        THIS TIME is the fact worth recording — overwriting a stale stamp
        from a prior attempt is correct, not a bug;
    (b) setdefault would let a model that happens to emit a field named
        `llm_backend` in its extracted JSON spoof its own provenance —
        assignment always stomps any model-authored value with the
        resolved truth.

    Fail-open: called right before the checkpoint is handed off to
    store/write, so a resolver exception must never fail an otherwise-
    successful serialize. Both fields are simply left absent.
    """
    try:
        backend = configure.resolved_backend()
    except Exception:
        log.warning("llm provenance stamp: resolved_backend() raised — "
                    "leaving llm_backend/llm_model absent")
        return
    checkpoint["llm_backend"] = backend
    try:
        model = config.llm_model()
    except Exception:
        model = None
    if model:
        checkpoint["llm_model"] = model


def serialize_strict(session_id: str, messages, chat=None, deadline=None) -> dict:
    """Transcript -> validated checkpoint, or a named SerializeError.

    `chat` is an injectable callable (messages, **kwargs) -> str; defaults to the
    real LLM client. `deadline` (time.monotonic() seconds) is the caller's
    budget for ONE wave of LLM work; chunked serializes scale it by the wave
    plan (#314: chunk batches + merge levels) before forwarding to the client,
    so the merge never starts starved by construction.

    Rendered transcripts over DAIMON_CHUNK_LINES go chunked (armC): per-chunk
    D-007 serialize -> 01c merge -> validate. Shorter ones stay single-pass.
    """
    if chat is None:
        chat = llm.chat
    # #359: tool rows are evidence, not conversation — they never count
    # toward the too-short gate, so surfacing them cannot let a two-turn
    # session sneak past it.
    n = sum(1 for m in messages or []
            if not (isinstance(m, dict) and m.get("tool_result")))
    if n < config.min_messages():
        raise TooShortError(
            f"transcript too short ({n} < {config.min_messages()} messages)"
        )
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise LLMCallError("deadline exhausted before the first LLM call")

    transcript_text = _render_transcript(messages)
    chunks = chunk_transcript(transcript_text, config.chunk_lines(), config.chunk_overlap())

    # #314: DAIMON_TIMEOUT is the field-derived floor for ONE slow call (#284,
    # scar #14) — but a chunked serialize is a PLAN of sequential waves (chunk
    # batches + merge levels). Sharing one single-call budget across the plan
    # guaranteed a starved merge on slow gateways, so scale the total by the
    # wave count. Per-call socket timeouts stay capped at the base budget
    # (llm.py), so no single request grows past gateway ceilings (scar #17).
    if deadline is not None and len(chunks) > 1:
        waves = _plan_waves(len(chunks), config.chunk_concurrency(),
                            config.merge_group_size())
        if waves > 1:
            extra = (waves - 1) * config.timeout_seconds()
            deadline += extra
            log.info("chunked call plan: %d wave(s) — deadline extended by %ds (#314)",
                     waves, extra)

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
                chat, _serialize_sys(),
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
                # #48: reuse any prior run's paid-for output for this exact
                # chunk text under the current config — merge deaths, heals,
                # resume forks, and grown transcripts all hit on their
                # unchanged prefix chunks.
                key = _chunk_cache_key(chunk_text)
                cached = _load_chunk_cache(key)
                if cached is not None:
                    log.info("chunk %d/%d reused cached extraction (#48)",
                             i + 1, len(chunks))
                    return cached
                t0 = time.monotonic()
                partial = _call_and_parse(
                    chat, _serialize_sys(),
                    f"session_id: {session_id}\n\n"
                    f"TRANSCRIPT (chunk {i + 1} of {len(chunks)}):\n{chunk_text}",
                    deadline, f"chunk {i + 1} of {len(chunks)}",
                )
                log.info("chunk %d/%d done in %.0fs",
                         i + 1, len(chunks), time.monotonic() - t0)
                _save_chunk_cache(key, partial)
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
    strip_code_owned_keys(checkpoint)
    checkpoint["session_id"] = session_id
    if not validate(checkpoint):
        log.info("checkpoint failed validation — one resample with attempt nonce (#118)")
        checkpoint = _produce(_RETRY_NOTE)
        strip_code_owned_keys(checkpoint)
        checkpoint["session_id"] = session_id
    if not validate(checkpoint):
        raise SchemaValidationError(
            "checkpoint failed schema/trust validation (missing keys, bad trust "
            "class, or verbatim item without a quote)"
        )
    sanitize_importance(checkpoint)
    sanitize_scene(checkpoint)
    # #358: translate cited [mN] markers to host message ids and drop any id
    # the transcript cannot vouch for — BEFORE verification, so verify_quotes
    # only ever sees code-validated bindings. #359: signal pointers (ids of
    # tool-result messages) survive on any item, evidence for outcome claims.
    sig_ids = signal_message_ids(messages)
    sanitize_source_ids(checkpoint, message_id_map(messages), sig_ids)
    # #125: verify verbatim quotes against the SAME rendered text the extractor
    # read, PRE-redaction (redaction runs later in write_checkpoint and would
    # otherwise mass-downgrade legitimate quotes it had masked). Verify once,
    # stamp the verdict — the briefing never re-greps. #358: items with a
    # validated binding resolve their id and compare bytes against just that
    # message, whole-transcript scan as fallback.
    verify_quotes(checkpoint, transcript_text, messages)
    # #359: derive the code-owned `grounded` verdict AFTER verification (it
    # must judge the surviving bindings) — outcome claims with a validated
    # signal pointer are marked grounded; unwitnessed verbatim outcome
    # claims in a signal-bearing session store as inferred.
    ground_outcomes(checkpoint, sig_ids)
    # #230: stamp provenance last, immediately before hand-off to store/write —
    # after validation/verification so it can never influence either, and
    # last so nothing downstream re-derives or clobbers it.
    _stamp_llm_provenance(checkpoint)
    # #48: success does NOT consume the chunk cache — persistence across
    # successful serializes is the feature (grown transcripts and resume
    # forks reuse their prefix chunks). Age-based reaping bounds the store.
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
