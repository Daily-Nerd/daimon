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

from . import config, configure, ledger, llm, redact, schema

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
# D-016 -> D-017 (#416: rule 21 asks extraction to PREFER a quote span that
# preserves a temporal span the transcript states — pure data-gathering for
# the deferred bi-temporal design (#406), NO new schema field or storage. The
# EXTRACTION_VERSION bump below rotates the #48 chunk-cache key so pre-#416
# cached extractions, which never tried to keep a date, can never satisfy a
# post-#416 request — a later re-measurement of the citable-date rate must
# reflect the new behavior, not stale accidental survival.)
# Checkpoints are only comparable across runs sharing this version (scar
# landmine #4); pre-bump checkpoints firing the #93 format_version mismatch
# warning is desired, not a bug.
PROMPT_VERSION = "D-018"

# #367: the chunk-cache rotation lever, deliberately SEPARATE from
# PROMPT_VERSION. Bump ONLY when extraction semantics change — the output
# contract, or what a chunk pass extracts (a D-016-grade change like #359's
# rule 20 warrants a bump; a wording clarification does not). PROMPT_VERSION
# keeps versioning the checkpoint format; this keeps the #48 cache warm
# across prompt edits that don't change what gets extracted.
# 1 -> 2 (#416): rule 21 changes which span extraction selects for a quote
# (it now prefers one that keeps a stated temporal span) — that is what a
# chunk pass extracts, so the cache must rotate. Serving a pre-#416 cached
# extraction that dropped the date would poison the later citable-date
# re-measurement the whole change exists to make honest.
# 2 -> 3 (#527): rule 22 adds the `because` field to decisions — a new
# extraction target, so a cached pre-#527 chunk would silently produce
# because-less decisions forever. First bump since the #514 checkpoint
# stamp shipped, so the corpus generation split is finally visible.
EXTRACTION_VERSION = 3


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

21. TEMPORAL SPANS: when the transcript states a date, time, or interval that is relevant
    to an item — when something happened, is due, or holds true (e.g. "on 2026-08-14", "by
    Friday", "from March to June", "during the Q3 freeze") — PREFER a contiguous quote span
    that KEEPS that temporal detail over an equally-valid span that drops it. The chosen span
    must still obey rule 17: a real, contiguous, verifiable copy-paste — never widen a span
    past what verifies just to reach a date, and never invent, normalize, or infer a date the
    transcript does not state. Add nothing when no temporal detail is present; this rule only
    stops you from discarding one that is.

22. THE BECAUSE CLAUSE (D-018): when the transcript STATES the reasoning behind a decision
    ("chose X because Y", "X — the alternative would have Z"), put ONE short clause of that
    stated reasoning in the decision's `because` field. Reasoning only, not a restatement of
    the decision. If the transcript states no reasoning, OMIT the field entirely — a decision
    without a stated why must arrive without one, never with an invented one. Same honesty
    bar as rule 1.

Schema shape:
{
  "session_id": "<id>",
  "working_context": {
    "active_topic": {"text": "", "trust": "", "quote": "", "importance": 0},
    "open_questions": [{"text": "", "trust": "", "quote": "", "external_state": false, "importance": 0}],
    "recent_decisions": [{"text": "", "trust": "", "quote": "", "because": "", "importance": 0, "links": [{"type": "", "target": ""}]}]
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

15. TEMPORAL SPANS: when two versions of the same item differ only in whether the quote keeps
    a date, time, or interval, keep the version whose quote PRESERVES that temporal detail — it
    is the more specific quote. Never invent, alter, or normalize a date while merging; this
    rule only prevents dropping a temporal detail a chunk already captured.

- BECAUSE clauses (D-018): when partial checkpoints hold the same decision and one carries a
  `because`, keep it. Never merge two reasons into a new sentence and never invent one for a
  decision that arrived without.

Schema shape:
{
  "session_id": "<id>",
  "working_context": {
    "active_topic": {"text": "", "trust": "", "quote": "", "importance": 0},
    "open_questions": [{"text": "", "trust": "", "quote": "", "external_state": false, "importance": 0}],
    "recent_decisions": [{"text": "", "trust": "", "quote": "", "because": "", "importance": 0, "links": [{"type": "", "target": ""}]}]
  },
  "epistemic_snapshot": {
    "strong_beliefs": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "uncertainties": [{"text": "", "trust": "", "quote": "", "importance": 0}],
    "contradictions_flagged": []
  },
  "worker_queue": []
}"""

# ---- #360: perspective-diverse escalation (heal-path only) ----
#
# The default serialize is deliberately ONE shape: chunk fan-out under one
# prompt, one merge pass. When it fails, heal used to retry that same shape —
# same perspective, same blind spots, just again (now with cached chunks,
# #48). Escalation is the heal tier: N extraction passes over the same
# transcript from DISTINCT perspectives (stage 1), combined by the ordinary
# merge pass (stage 2). The merge model is a PRODUCER, never a verifier
# (scar #10): everything still crosses the existing deterministic gates —
# sanitize_source_ids, verify_quotes, ground_outcomes, redaction downstream —
# byte-for-byte unchanged.
#
# Each addendum is APPENDED to the full base prompt (composing with the #317
# scene appendix), so every extraction rule — quote discipline, source
# message ids, outcome grounding — stays in force in every pass. Triggered
# only via serialize_strict(escalate=True), which only cli's heal path passes
# (behind DAIMON_HEAL_ESCALATION): token cost scales with failure, never with
# usage.

ESCALATION_PERSPECTIVES = (
    ("decisions-and-outcomes", """

EXTRACTION PERSPECTIVE — DECISIONS AND OUTCOMES: this pass is one of several
independent passes over the same transcript, each reading from a different
angle; other passes cover open questions and artifacts in depth. YOUR angle:
be EXHAUSTIVE on recent_decisions — explicit user choices, terse
ratifications, [Fix]/[Diagnosis] items, implementation-level decisions — and
on claims that assert an outcome (succeeded, failed, merged, deployed, tests
green), each with its tool-result evidence cited per rule 20. Still populate
every schema section (the shape is mandatory) and extract items outside your
angle when they are clearly load-bearing, but spend your effort here. Every
rule above stays in force."""),
    ("open-loops-and-questions", """

EXTRACTION PERSPECTIVE — OPEN LOOPS AND QUESTIONS: this pass is one of
several independent passes over the same transcript, each reading from a
different angle; other passes cover decisions and artifacts in depth. YOUR
angle: be EXHAUSTIVE on open_questions and uncertainties — things left
genuinely unresolved, work the assistant said it would do "next" or "after",
verifications that never happened, optional follow-ups explicitly flagged,
stated doubts, and anything whose answer may have changed outside the session
(mark external_state per rule 10). Still populate every schema section (the
shape is mandatory) and extract items outside your angle when they are
clearly load-bearing, but spend your effort here. Every rule above stays in
force."""),
    ("artifacts-and-identifiers", """

EXTRACTION PERSPECTIVE — ARTIFACTS AND IDENTIFIERS: this pass is one of
several independent passes over the same transcript, each reading from a
different angle; other passes cover decisions and open questions in depth.
YOUR angle: be EXHAUSTIVE about concrete artifacts and their EXACT
identifiers — repos as owner/name, issues/PRs as owner/name#123 or full
URLs, file paths, function and symbol names, commit hashes, version numbers,
package names with versions, ports, counts and ranges — copied exactly per
rules 13 and 18 and attached to the items they belong to. Never invent an
identifier the transcript does not contain. Still populate every schema
section (the shape is mandatory) and extract items outside your angle when
they are clearly load-bearing, but spend your effort here. Every rule above
stays in force."""),
)


def escalation_systems() -> list[tuple[str, str]]:
    """(name, full system prompt) per perspective — the base serialize prompt
    (scene appendix included when flagged) plus that perspective's addendum,
    so escalation composes with #317 instead of forking it."""
    base = _serialize_sys()
    return [(name, base + addendum) for name, addendum in ESCALATION_PERSPECTIVES]


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


def _item_reason(item) -> str | None:
    """None when the item is valid, else WHICH predicate rejected it (#555).

    Names the predicate only. Never the item's text, quote, or trust value —
    the reason is logged verbatim and the payload is exactly what the boundary
    is refusing to vouch for.
    """
    if not isinstance(item, dict):
        return "not a dict"
    if "text" not in item or "trust" not in item:
        return "missing text or trust"
    # #134: presence != a usable value. A present-but-null (or non-str) text
    # passed this check, reached disk, then crashed briefing.render on the next
    # session. Reject at the boundary. Empty str stays valid — active_topic MAY
    # carry empty text (test_validate_allows_empty_active_topic_text).
    if not isinstance(item["text"], str):
        return "text is not a str"
    if item["trust"] not in _TRUST_CLASSES:
        return "trust is not a known trust class"
    if item["trust"] == "verbatim":
        quote = item.get("quote")
        # D-006: a verbatim claim without a real quote is an unpinned claim.
        if not isinstance(quote, str) or not quote.strip():
            return "trust=verbatim item has no quote"
    because = item.get("because")
    if because is not None and not isinstance(because, str):
        # F4 (#527), same #134 lesson as text: present-but-non-str would
        # reach disk and crash a later render — reject at the boundary.
        return "because is not a str"
    anchor = item.get("anchored_to")
    if anchor is not None:
        if not isinstance(anchor, dict):
            return "anchored_to is not a dict"
        if not all(
            isinstance(anchor.get(k), str) and anchor.get(k)
            for k in ("file", "symbol", "body_hash")
        ):
            return "anchored_to is missing file, symbol, or body_hash"
    return None


def _valid_item(item) -> bool:
    return _item_reason(item) is None


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


def validation_reason(checkpoint) -> str | None:
    """None when the checkpoint is valid, else WHICH predicate rejected it.

    Light validation: required keys + trust-class integrity (D-006). Not full
    JSON-schema validation — just enough to refuse garbage before storing.

    Contract: active_topic MAY have empty text (sessions without a single clear
    topic); briefing.render() skips the empty section. Trust rules still apply.

    #555: the bool this used to return threw away the one fact a failure is
    diagnosed by. The causes are not equivalent — a bad trust class means the
    model ignored an instruction, a verbatim item with no quote means D-006
    caught a genuinely unpinned claim (the machinery working), a non-str text
    means junk arrived from upstream. Predicate order is unchanged, so the
    accept/reject verdict is byte-identical to the bool version.
    """
    if not isinstance(checkpoint, dict):
        return "checkpoint is not a dict"
    if "session_id" not in checkpoint:
        return "missing session_id"
    wc = checkpoint.get("working_context")
    es = checkpoint.get("epistemic_snapshot")
    if not isinstance(wc, dict):
        return "working_context is not a dict"
    if not isinstance(es, dict):
        return "epistemic_snapshot is not a dict"
    if "active_topic" not in wc:
        return "missing working_context.active_topic"
    for key in ("open_questions", "recent_decisions"):
        items = wc.get(key)
        if not isinstance(items, list):
            return f"working_context.{key} is not a list"
        for i, item in enumerate(items):
            reason = _item_reason(item)
            if reason is not None:
                return f"working_context.{key}[{i}]: {reason}"
    reason = _item_reason(wc["active_topic"])
    if reason is not None:
        return f"working_context.active_topic: {reason}"
    for key in ("strong_beliefs", "uncertainties"):
        items = es.get(key, [])
        if not isinstance(items, list):
            return f"epistemic_snapshot.{key} is not a list"
        for i, item in enumerate(items):
            reason = _item_reason(item)
            if reason is not None:
                return f"epistemic_snapshot.{key}[{i}]: {reason}"
    return None


def validate(checkpoint) -> bool:
    """True when the checkpoint passes validation_reason()'s predicates."""
    return validation_reason(checkpoint) is None


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
    is unverifiable and returns False (conservative: never auto-pass).

    A `[redacted:...]` marker is a fragment boundary, exactly like an ellipsis
    (#505). Stored quotes are redacted at capture while the transcript still
    holds the real secret, so the marker stands where bytes we cannot reproduce
    used to be — the same situation as an elided span. DELETING it instead
    (the pre-#505 behavior) joined two spans that are not adjacent in the
    source, so any quote with a secret in the MIDDLE could never match: the
    contract the docstring described was only ever met at the quote's edges."""
    if not isinstance(quote, str) or not isinstance(haystack, str):
        return False
    hay = _normalize_for_match(haystack)
    fragments = []
    for raw in _ELLIPSIS_RE.split(quote):
        for piece in _REDACTED_RE.split(raw):
            frag = _normalize_for_match(piece)
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


# ---- #440: daimon's own injected output is not a witness ----
#
# The recall hook (`daimon recall: prior work — ...`) and the SessionStart
# briefing (`DAIMON BRIEFING ...`) print INTO the host's transcript, and
# transcript.py flattens hook stdout byte-identically into the user turn that
# carried it — under the SAME message id as the user's own prose (whole-turn
# granularity, #358). A quote copied out of that echo used to pass
# verification and store as trust="verbatim", quote_verified: true: a PRIOR
# session's item laundered as freshly witnessed in THIS one.
#
# The strip is applied to the verification haystacks ONLY. `_render_transcript`
# deliberately keeps feeding the extractor the raw text: the brief legitimately
# informs the model, and #48 chunk-cache keys derive from the chunk text, so
# stripping there would invalidate every cached extraction on every install.
# The laundering happens at verification, so it is fixed at verification.

# Injected scaffolding rides inside user turns wrapped in <system-reminder>.
# Quoting it back is daimon vouching for its own output (the self-reference
# loop, parked 07-15); `pin_imperatives` (#369) has always stripped it for its
# own scan, and this is that same strip promoted to shared use.
_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>",
                                 re.DOTALL | re.IGNORECASE)
# Line-scoped: a recall injection is exactly one line and never spans a
# newline, so a genuine user sentence on the next line keeps its witness.
_RECALL_LINE_RE = re.compile(r"daimon recall: prior work —[^\n]*")
# Message-scoped: the briefing is a multi-line render with no terminator, so
# everything from its first marker to the end of the MESSAGE goes. Either
# marker fires on its own — a host that swallows the hook's `DAIMON BRIEFING`
# header still emits the render's own first line. A deliberate
# over-approximation: over-stripping costs a downgrade to inferred,
# under-stripping mints a false `verbatim`, and only one of those is a
# security bug.
_BRIEF_HEAD_RE = re.compile(
    r"(?:DAIMON BRIEFING |While you were away — here['’]s where we left off\.).*",
    re.DOTALL)

# Code-owned, absent-never-False (#292 discipline, same as `grounded`): set
# only where verify_quotes proves the quote lives in injected output and
# nowhere else, so `verification_rejections` can ledger it under its own
# reason code and the echo rate becomes an endogenous measurement.
ECHO_ONLY_KEY = "quote_echo_only"


def strip_injected(text: str) -> str:
    """`text` minus daimon's own injected spans — ONE message's text.

    The briefing rule truncates to end of string, which is end of MESSAGE by
    contract: every caller hands this a single message body. The
    whole-transcript haystack goes through `stripped_transcript`, which strips
    per message and re-renders, so a briefing never swallows the turns after
    it. Non-str input yields "" — an unusable haystack fails closed."""
    if not isinstance(text, str):
        return ""
    text = _SYSTEM_REMINDER_RE.sub(" ", text)
    text = _RECALL_LINE_RE.sub("", text)
    return _BRIEF_HEAD_RE.sub("", text)


def daimon_output_ids(messages) -> set:
    """Ids of rows transcript.py flagged as daimon's own tool output (#512):
    a tool result born from a daimon invocation (CLI or MCP), resolved by
    provenance at parse time. Verification must never read these as
    witnesses — same principle as strip_injected, keyed on the invocation
    instead of the render shape, so every daimon subcommand present or
    future is covered without pattern enumeration."""
    return {mid for m in (messages or [])
            if isinstance(m, dict) and m.get("daimon_output")
            and (mid := _message_id(m)) is not None}


def extraction_messages(messages):
    """Messages as the EXTRACTOR should see them: daimon's own tool output
    blanked (#577).

    Until this existed the two halves disagreed. `stripped_transcript` blanked
    a `daimon_output` row so quote verification could not accept daimon as a
    witness for its own claim, while the extractor kept reading the same rows
    raw. Verification is quote-scoped, so an item could carry a real assistant
    sentence in `quote` and daimon's own output in `text`/`because`/`scene`,
    and arrive at `verbatim` with content the defense was built to keep out.
    Measured on #575's guard: one derived belief was correctly downgraded to
    `inferred`, one derived decision landed `verbatim`.

    Blanked, never dropped: `[mN]` markers are positional over the full list,
    so removing a row would renumber every later citation.

    What this costs, measured rather than assumed. Chunk-cache keys derive from
    chunk text (#48), so blanking shifts chunk boundaries and invalidates
    entries: 28.6% of chunks over 1,166 real transcripts, against a cache that
    reaps itself every `chunk_cache_days` (3). It is a one-time re-extraction
    of a store that fully rotates twice a week.

    What it does NOT cost: content. Of 616 items across 12 checkpoint chains,
    156 had text appearing verbatim in the brief rendered from their own prior
    checkpoint, and 156 of 156 were already stored in that bucket. Nothing here
    is the only copy of anything. Carry works off checkpoint data, not off the
    extractor's input, so carried items are untouched either way.

    Open and deliberately not claimed: whether the brief as CONTEXT helps the
    extractor interpret the session's own content. Substring matching cannot
    see reworded reuse, and no retrospective can measure a context effect.
    """
    return [dict(m, content="") if isinstance(m, dict) and m.get("daimon_output")
            else m for m in (messages or [])]


def stripped_transcript(messages) -> str:
    """`_render_transcript`'s text with every message's injected spans removed
    — the whole-transcript VERIFICATION haystack (#440).

    Renders shallow message copies whose flattened text has been stripped, so
    markers, role labels and joins stay byte-identical to the haystack
    verification read before this fix: only the injected bytes go missing.
    #512: a daimon-output tool row is BLANKED, never dropped — [mN] markers
    are positional over the full list and a dropped row would renumber every
    later citation.

    No non-dict guard on purpose: `serialize_strict` renders the SAME list
    through `_render_transcript` before verification runs, and that raises on
    any non-dict row — so a message list that reaches here has already proven
    itself dict-shaped. A guard would only move a crash that already
    happened."""
    stripped = []
    for m in messages or []:
        copy = dict(m)
        copy["content"] = ("" if m.get("daimon_output")
                           else strip_injected(_message_text(m)))
        stripped.append(copy)
    return _render_transcript(stripped)


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
    it.

    #440: both haystacks are stripped of daimon's OWN injected output first
    (see strip_injected) — a quote copied out of a recall line or a briefing
    block is an echo, not a witness, and must never earn `verbatim`. A miss
    is re-checked against the unstripped text so an echoed quote is ledgered
    under `echo-only` rather than the generic absent-quote reason."""
    # #440: the RAW pair survives alongside the stripped haystacks, read on
    # the failure path only — to tell an echoed quote from an absent one.
    # #512: daimon-output tool rows are blanked in the STRIPPED map (their
    # bytes are daimon's own, not a witness) but kept raw, so a quote living
    # only there downgrades under the honest `echo-only` reason code.
    raw_texts_by_id = message_texts_by_id(messages) if messages else {}
    daimon_ids = daimon_output_ids(messages) if messages else set()
    texts_by_id = {mid: "" if mid in daimon_ids else strip_injected(text)
                   for mid, text in raw_texts_by_id.items()}
    haystack = (stripped_transcript(messages) if messages
                else strip_injected(transcript_text))
    # #359: signal pointers (tool-result ids) are outcome evidence, not
    # quote-source claims — they never scope the quote check, and a scoped
    # MISS must not execute them for the quote-id's crime.
    signals = signal_message_ids(messages) if messages else set()
    downgraded = echoed = 0
    # The model never gets a vote on the echo verdict (#292 discipline, same
    # as `grounded`/`pinned`): any model-emitted value is dropped before the
    # checker re-derives it.
    for item in iter_items(checkpoint):
        item.pop(ECHO_ONLY_KEY, None)
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
        elif quote_matches(quote, haystack):
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
            # #440: a second pass over the UNSTRIPPED text separates "present
            # only in daimon's own injected output" from "absent entirely".
            # Same downgrade either way — an echo is not a witness — but the
            # distinct reason code turns the echo rate into something the
            # rejection ledger can count.
            raw_scoped = scoped_haystack(item, raw_texts_by_id, exclude=signals)
            if ((raw_scoped is not None and quote_matches(quote, raw_scoped))
                    or quote_matches(quote, transcript_text)):
                item[ECHO_ONLY_KEY] = True
                echoed += 1
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
            if item.get(ECHO_ONLY_KEY):
                log.warning("quote verification: downgraded verbatim->inferred "
                            "(echo-only: quote appears only in daimon's own "
                            "injected output): %s", logged)
            else:
                log.warning("quote verification: downgraded verbatim->inferred: %s",
                            logged)
    if downgraded:
        log.info("quote verification: %d verbatim item(s) downgraded to inferred"
                 " (%d echo-only)", downgraded, echoed)
    return downgraded


# ---- #480 slice 3: agent resolve-candidate evidence, verified at serialize ----
#
# `daimon resolve --by agent --evidence "<quote>"` (#482, slice 2) appends a
# `resolving-candidate` event that never withholds anything on its own — the
# quote is only a CLAIM until this pass byte-checks it against the session's
# own transcript, at serialize time, the same way verify_quotes byte-checks a
# verbatim capture claim. Deliberately the SAME matching stack (quote_matches,
# strip_injected/stripped_transcript) rather than a second one: an agent's
# evidence meets the identical bar a capture claim's quote meets.

def verify_agent_evidence(evidence, messages, haystack=None) -> tuple:
    """Byte-check ONE agent resolve-candidate's evidence quote against a
    transcript. Returns (found, role).

    `found` is whether the quote's bytes are present in the (stripped)
    transcript, under quote_matches' tier-f normalization. `role` is the
    `role` of the single message whose own stripped text contains the quote,
    when exactly that is determinable; "unknown" when the quote isn't found
    at all, when no single message's text contains it (e.g. real bytes but
    only found once messages are joined into the whole-transcript haystack),
    or when the carrying message has no usable role. Per the design doc's
    self-quotation section: this is labeling, not gating — an assistant-only
    quote is exactly as trustworthy as any other assistant assertion, and the
    caller records the role rather than hiding or blocking on it.

    `haystack` lets a caller checking several candidates against the SAME
    session's transcript build the stripped whole-transcript haystack once
    (stripped_transcript is O(messages)) instead of once per candidate; omit
    it to have this function build it.

    Blank/non-str evidence never matches — mirrors verify_quotes' own
    "no usable quote" guard, conservative by construction."""
    if not isinstance(evidence, str) or not evidence.strip():
        return False, "unknown"
    if haystack is None:
        haystack = stripped_transcript(messages) if messages else ""
    if not quote_matches(evidence, haystack):
        return False, "unknown"
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if quote_matches(evidence, strip_injected(_message_text(m))):
            role = m.get("role")
            return True, (role.strip()
                         if isinstance(role, str) and role.strip() else "unknown")
    return True, "unknown"


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

# Conservative, English outcome lexicon: past-tense/state assertions about
# completion. A curated, BOUNDED-regex allowlist (scar 22: no unbounded prefix
# before a keyword alternation) — never stemming, never an LLM call.
_OUTCOME_RE = re.compile(
    r"(?:\b(?:succeeded|successfully)\b"
    r"|\btests?\s+(?:all\s+|are\s+|now\s+)*(?:pass(?:ed|ing)?|green)\b"
    r"|\b(?:build|suite|ci|pipeline|deploy(?:ment)?)\s+"
    r"(?:is\s+|now\s+)*(?:pass(?:ed|ing)?|green|succeeded|failed|completed)\b"
    r"|\b(?:merged|deployed|released|published|shipped|landed)\b"
    r"|\ball\s+(?:\d+\s+)?tests?\s+pass(?:ed)?\b)",
    re.IGNORECASE)
# Spanish outcome lexicon (#401): the project is bilingual end to end (docs, es
# briefings), but this gate was English-only, so a Spanish "los tests pasan"
# sailed through ungrounded while its English twin was downgraded. Same shape
# as the English lexicon above and the same house rules — a curated, bounded
# allowlist, NOT stemming, NOT an LLM. Participles carry Spanish gender/number
# agreement through a bounded (?:o|a|os|as) suffix; still fully bounded.
_OUTCOME_ES_RE = re.compile(
    r"(?:\bexitosamente\b"
    r"|\bcon\s+[eé]xito\b"
    r"|\b(?:los\s+|las\s+)?(?:tests?|pruebas)\s+"
    r"(?:ya\s+|ahora\s+|todas\s+)*(?:pasan|pasaron)\b"
    r"|\b(?:el\s+|la\s+)?(?:build|suite|ci|pipeline|despliegue|compilaci[oó]n)"
    r"\s+(?:ya\s+|ahora\s+)*(?:pas[oó]|complet[oó]|termin[oó]|fall[oó]|"
    r"en\s+verde)\b"
    r"|\b(?:mergead|fusionad|desplegad|publicad|lanzad|liberad|completad|"
    r"resuelt|arreglad|solucionad)(?:o|a|os|as)\b"
    r"|\bfall(?:[oó]|aron|id[oa]s?)\b"
    r"|\ben\s+verde\b"
    r"|\btodas\s+(?:las\s+)?(?:\d+\s+)?pruebas\s+pas(?:an|aron)\b)",
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
# Spanish hedge markers (#401), mirror of _HEDGE_RE: future ("se va a mergear"),
# plan ("planeo", "pendiente", "falta"), condition/question ("si", "cuando").
_HEDGE_ES_RE = re.compile(
    r"\b(?:se\s+van?\s+a|van?\s+a|vamos\s+a|ser[aá]n?|"
    r"plane(?:o|as|amos|ad[oa]s?)|pendiente|"
    r"todav[ií]a\s+no|a[uú]n\s+no|falta[nr]?|hay\s+que|hace\s+falta|"
    r"deber[ií]an?|debe[n]?|podr[ií]an?|quiz[aá]s?|tal\s+vez|"
    r"si|cuando|una\s+vez|acaso|a\s+punto\s+de|por\s+hacer)\b",
    re.IGNORECASE)


def _asserts_outcome(text: str) -> bool:
    """True when `text` ASSERTS a completed outcome (narrow; English + Spanish).

    Mirrors both language lexicons: a claim asserts an outcome only when a
    curated verb matches AND no hedge (either language) is present — a Spanish
    hedge blocks an English claim too, which is the conservative default."""
    if not isinstance(text, str):
        return False
    if _HEDGE_RE.search(text) or _HEDGE_ES_RE.search(text):
        return False
    return bool(_OUTCOME_RE.search(text) or _OUTCOME_ES_RE.search(text))


def verification_rejections(checkpoint) -> list:
    """Every rejection the checkers made on this checkpoint, as ledger rows
    (#376): [{item_ref, check, reason}].

    Derived from the finished checkpoint rather than observed at the call
    site, because `serialize_strict` has no project context and the ledger
    write needs one. That is sound, not a shortcut: `quote_verified: False`
    is written ONLY where verify_quotes downgrades (a passing item gets
    True, an already-inferred item is left untouched with neither field),
    and `grounded: False` ONLY where ground_outcomes downgrades. The stored
    state is therefore a faithful record of both rejection acts.

    An item with no `id` yields no row: a ledger entry nobody can trace back
    is noise. Never raises — this feeds an advisory counter."""
    out = []
    for item in iter_items(checkpoint):
        ref = item.get("id")
        if not isinstance(ref, str) or not ref:
            continue
        if item.get("quote_verified") is False:
            # #440: the two ways a quote fails are worth telling apart —
            # "nowhere in the session" is a fabrication signal, "only inside
            # daimon's own injected output" is the echo rate.
            out.append({"item_ref": ref, "check": "quote",
                        "reason": ("echo-only"
                                   if item.get(ECHO_ONLY_KEY) is True
                                   else "quote-not-in-transcript")})
        if item.get(GROUNDED_KEY) is False:
            out.append({"item_ref": ref, "check": "outcome",
                        "reason": "no-signal-cited"})
    return out


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


# ---- #369: deterministic auto-pin for hard-imperative constraints ----
#
# Verbatim trust-class assignment is model-chosen: if the model paraphrases a
# "must not X" into summary prose, no quote exists, verification has nothing
# to check, and later softening is undetectable. Constraint inversion is the
# highest-damage drift class — a "never" that comes back as "usually" silently
# reweights every downstream decision. This pass is the deterministic
# backstop: scan USER text for hard-imperative sentences and force-pin any
# the model skipped as verbatim items, bound to their message ids like any
# other quote. Tiering bounds noise: only hard imperatives pin (must / must
# not / never / don't / always / forbidden); soft modals (should, could,
# prefer) stay at model discretion. The whole value is a check that needs no
# model to trust.

PINNED_KEY = "pinned"

# Hard imperatives are rare per session; the cap bounds pathological inputs
# (a pasted style guide) so the verification matcher's workload stays flat.
_MAX_AUTO_PINS = 10
_PIN_MAX_CHARS = 300

_IMPERATIVE_RE = re.compile(
    r"\b(?:must(?:\s+not)?|never|do\s+not|don['’]t|always|forbidden)\b",
    re.IGNORECASE)

# Injected scaffolding (briefings, hook output) rides inside user turns
# wrapped in <system-reminder> — pinning it would quote daimon's own output
# back as a user constraint (the self-reference loop, parked 07-15). The
# pattern itself now lives with the #440 strip family above, which generalized
# this guard from the auto-pin scan to the verification haystacks.


def _constraint_sentences(text: str):
    """Sentences worth considering for a pin: terminator kept (the quote must
    match the transcript byte-for-byte under tier-f normalization), leading
    list markers shed, questions and fragments dropped."""
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = raw.strip().lstrip("-*>•# ").strip()
        if not s or s.endswith("?"):
            continue
        if len(s) > _PIN_MAX_CHARS or len(s.split()) < 3:
            continue
        yield s


def pin_imperatives(checkpoint, messages) -> int:
    """Force-pin hard-imperative user constraints the model skipped (#369).
    Returns the number of items added.

    Runs AFTER sanitize_source_ids (host ids are inserted directly, never
    marker-translated) and BEFORE verify_quotes, so every pin earns its
    quote_verified/last_verified stamps through the same gauntlet as a
    model-chosen quote — a pin is self-verifying by construction (the
    sentence came from the transcript), and a broken one must fail loudly
    there, not pass silently here.

    Only user-authored text is scanned: assistant imperatives are
    commentary, tool rows are payloads, and <system-reminder> spans are
    daimon's own injected output. `pinned` is code-owned (#292 discipline,
    same as `grounded`): model-emitted values are stripped first. Never
    fatal — pure text scanning and dict appends."""
    for item in iter_items(checkpoint):
        item.pop(PINNED_KEY, None)
    existing = [q for q in (
        _normalize_for_match(item.get("quote"))
        for item in iter_items(checkpoint)
        if item.get("trust") == "verbatim"
        and isinstance(item.get("quote"), str))
        if q]
    block = checkpoint.get("epistemic_snapshot")
    if not isinstance(block, dict):
        return 0
    beliefs = block.get("strong_beliefs")
    if not isinstance(beliefs, list):
        beliefs = block["strong_beliefs"] = []
    pinned = dropped = 0
    seen: set = set()
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        if m.get("tool_result"):
            continue
        text = _SYSTEM_REMINDER_RE.sub(" ", _message_text(m))
        mid = _message_id(m)
        for sentence in _constraint_sentences(text):
            if not _IMPERATIVE_RE.search(sentence):
                continue
            norm = _normalize_for_match(sentence)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            # The model already pinned it (quote covers the sentence, or the
            # sentence covers the quote): the backstop has nothing to add.
            if any(norm in q or q in norm for q in existing):
                continue
            if pinned >= _MAX_AUTO_PINS:
                dropped += 1
                continue
            item = {"text": sentence, "trust": "verbatim", "quote": sentence,
                    PINNED_KEY: True}
            if mid is not None:
                item[SOURCE_IDS_KEY] = [mid]
            beliefs.append(item)
            pinned += 1
    if dropped:
        # No silent caps: a constraint that didn't pin must be visible.
        log.warning("imperative auto-pin: cap %d reached, %d constraint "
                    "sentence(s) not pinned", _MAX_AUTO_PINS, dropped)
    if pinned:
        log.info("imperative auto-pin: %d hard-imperative constraint(s) "
                 "force-pinned (#369)", pinned)
    return pinned


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
# (backend, model, temperature, scene flag, extraction-semantics version, and
# a lane label for perspective passes). Deliberately NOT keyed on prompt text
# or PROMPT_VERSION (#367): the prompt evolves nearly every release, and
# hashing it rotated the whole cache on wording-only edits — re-buying every
# chunk at full price, exactly the cost the cache exists to avoid.
# EXTRACTION_VERSION below is the deliberate rotation lever; the scene flag is
# an explicit dimension because it changes what gets extracted (#317).
# session_id is deliberately NOT in the key: prefix chunks of a grown or
# resume-forked transcript are byte-identical and their paid-for outputs
# transfer (#48). The prompt embeds a positional "chunk i of n" label that the
# key ignores — presentation metadata, not extraction semantics.
#
# Entries persist across successful serializes (that IS the feature) and are
# reaped by age. Cached output is PRE-redaction — forced by #125: quotes are
# verified against the pre-redaction transcript, and caching redacted output
# would mass-downgrade legitimate verbatim items on every hit. The rotation
# window (config.chunk_cache_days, default 3) is therefore a privacy bound;
# files are written 0600. Same sensitivity and root as checkpoints.
# Everything here is best-effort: a broken cache must never break a
# serialize — worst case is re-paying the chunk call.
#
# #465 producer verification (the same hole #343 closed in the bench cache,
# tests/bench/cache.py — this mirrors that contract). The `model` dimension in
# the key above is the CONFIGURED alias: routing config, not provenance (scar
# 0032). When the gateway silently substitutes, the client-side fallback flag
# stays False, so the substitute's extraction would land under the alias key
# and replay for chunk_cache_days. The served model can never join the key —
# it is unknowable before the call that produces it (chicken-egg) — so entries
# instead carry it in an envelope `{"served_model": ..., "partial": {...}}`
# and reads verify it against the run's #458 receipts (llm.served_models()):
#   - recorded producer disagrees with the run's single receipt -> MISS
#   - recorded null (command backend, honest absence — never the alias copied
#     in) replays ONLY into a run that is itself receiptless
#   - the run is already mixed -> MISS (fail toward re-serialize)
#   - the run has no receipts yet -> ADOPT the recorded producer via
#     llm.note_served, so a later live substitution trips both this check and
#     the existing _stamp_llm_provenance WARNING/counter
# Writes hold the poison gate (scar 0015): a mixed-producer run caches nothing.
# Pre-#465 entries are raw partials with no receipt — unattributable, so they
# are a counted miss and re-warm once.


def _chunk_cache_dir():
    return config.checkpoint_dir() / ".chunk-cache"


def _chunk_cache_key(chunk_text: str, lane: str = "default") -> str:
    # `lane` (#360, re-keyed by #367): escalation's perspective passes send
    # DIFFERENT prompts over the same chunks — the perspective NAME labels
    # each lane (no cross-contamination) while a re-escalation reuses its own
    # prior partials. A wording tweak to any prompt keeps every lane warm;
    # EXTRACTION_VERSION is the deliberate rotation. A forgotten bump serves
    # a stale extraction for at most chunk_cache_days (default 3) — the same
    # rotation window the privacy design already relies on.
    try:
        backend = configure.resolved_backend()
    except Exception:
        backend = "unknown"
    scene = "scene" if config.scene_traces_enabled() else ""
    stamp = (f"v2\x00{backend}\x00{config.llm_model() or ''}"
             f"\x00{config.llm_temperature()}\x00{EXTRACTION_VERSION}"
             f"\x00{scene}\x00{lane}\x00")
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
    if not isinstance(obj, dict):
        return None
    if "partial" not in obj:
        # Pre-#465 entry: a raw partial with no producer receipt, exactly the
        # unattributable shape a silently-substituted run leaves behind. Never
        # replayed; the cost is a one-time re-warm after upgrading, after
        # which every entry carries its receipt.
        _note_chunk_cache_miss("serialize:chunk-cache-legacy-miss")
        return None
    partial = obj.get("partial")
    if not isinstance(partial, dict):
        return None
    recorded = obj.get("served_model")
    if recorded is not None and not isinstance(recorded, str):
        return None  # corrupt receipt: unverifiable, so not replayable
    observed = llm.served_models()
    if recorded is None:
        # Receiptless entry (command backend): replayable only into a run that
        # is itself receiptless — a receipted run must not fold in content no
        # receipt can attribute.
        if observed:
            _note_chunk_cache_miss("serialize:chunk-cache-model-mismatch")
            return None
    elif len(observed) > 1:
        # Already mixed: nothing can be verified against a single producer, so
        # fail toward re-serialize rather than replay into the mess.
        _note_chunk_cache_miss("serialize:chunk-cache-model-mismatch")
        return None
    elif not observed:
        # First read before any live call — adopt the entry's producer as this
        # run's first receipt (#465; mirrors the bench cache's pin adoption).
        llm.note_served(recorded)
    elif recorded != observed[0]:
        _note_chunk_cache_miss("serialize:chunk-cache-model-mismatch")
        return None
    return partial


def _note_chunk_cache_miss(counter: str) -> None:
    # Counted through the SAME local usage-counter mechanism the cli commands
    # use (#54), so a producer-verification miss is never silently
    # indistinguishable from a cold cache. Lazy import keeps the module graph
    # acyclic (cli imports this module); best-effort keeps the read fail-open.
    try:
        from . import cli
        cli._note_usage(counter)
    except Exception:
        pass


def _save_chunk_cache(key: str, partial: dict) -> None:
    if not config.chunk_cache_enabled():
        return
    if llm.fallback_used():
        # #28/#343 lesson: once the weaker fallback backend has fired in this
        # process, nothing from this run may be cached under the primary
        # backend's key — that is exactly how caches get poisoned.
        return
    served = llm.served_models()
    if len(served) > 1:
        # #465: distinct producers observed in this process — no chunk is
        # attributable to a single model, and caching any of it under the
        # alias key is exactly how caches get poisoned (scar 0015). The write
        # gate stays THE gate.
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
        # #465 envelope: the producer rides alongside the payload. Exactly one
        # receipt names it; none is honest absence (null), never a default to
        # the configured alias (scar 0032).
        entry = {"served_model": served[0] if served else None,
                 "partial": partial}
        tmp = d / f".{key}.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(d / f"{key}.json")
    except OSError:
        pass


def purge_chunk_cache():
    """#422: wholesale removal of every cached chunk extraction. `daimon
    forget` calls this after a successful tombstone+rewrite: cached output is
    PRE-redaction (see the block comment above _chunk_cache_dir) and keyed by
    chunk TEXT plus config dimensions — a forgotten value cannot be located
    selectively, so the whole cache goes. Accepted cost: re-paying extraction
    for chunks younger than chunk_cache_days (default 3), the same window the
    age reaper already bounds. The directory itself is kept — the next
    serialize re-populates in place. Orphaned `.*.tmp` writer droppings carry
    the same pre-redaction bytes, so they go too.

    Returns (purged_count, error_or_None) and NEVER raises: the caller's
    deletion of belief state is the primary contract; a failed purge is
    reported, not fatal."""
    d = _chunk_cache_dir()
    if not d.is_dir():
        return 0, None  # nothing cached — vacuously purged
    purged, error = 0, None
    try:
        targets = sorted(set(d.glob("*.json")) | set(d.glob(".*.tmp")))
    except OSError as e:
        return 0, str(e)
    for entry in targets:
        try:
            entry.unlink()
            purged += 1
        except OSError as e:
            error = str(e)
    return purged, error


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
            ledger.touch_heartbeat(session_id)  # #342: alive, merging
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
    # #514: the extractor dates its own run at serialize time; a model never
    # gets to claim one (and the introspection path stays absent = unknown).
    "extraction_version",
    # #268: origin binding is asserted by policy.bind_origin at the write
    # boundary. No code path reads a top-level copy, but a model naming the
    # key must not leave one lying beside the real per-item stamps.
    "origin_session", "origin_author",
)

# The same discipline one level down: origin binds per ITEM (#268 S1), and
# policy.bind_origin uses setdefault so a carried item's binding survives a
# re-write. That makes any value already on an item authoritative forever —
# so a model-emitted one is a self-issued witness that corroboration counting
# would then treat as an independent session. Stripped from freshly authored
# model output only, exactly like `grounded`/`pinned` (#292 discipline).
# #511: `quote_verified`/`last_verified` are verify_quotes' verdicts — on the
# serialize path they are re-derived AFTER this strip, and on the introspection
# path (no transcript, verify_quotes never runs) nothing may hold either stamp.
# Safe to strip on both paths because carry.merge folds prev items in AFTER
# serialize_strict returns — a carried item's genuine stamps never pass here.
_CODE_OWNED_ITEM_KEYS = ("origin_session", "origin_author",
                         "quote_verified", "last_verified")


def strip_code_owned_keys(checkpoint: dict) -> None:
    """Discard any code-owned key a model emitted in its own output, at the
    checkpoint level (_CODE_OWNED_KEYS) and per item (_CODE_OWNED_ITEM_KEYS).

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
    for item in iter_items(checkpoint):
        for key in _CODE_OWNED_ITEM_KEYS:
            if item.pop(key, None) is not None:
                log.info("serialize: discarding model-supplied item key %r", key)


def downgrade_unverifiable_verbatim(checkpoint) -> int:
    """Downgrade every `trust: "verbatim"` item to `inferred`, in place, and
    return the count (#511). For paths with NO transcript (cli's
    `write-checkpoint` introspection path) — nothing there can byte-check a
    quote, so `verbatim` is a trust class the code cannot justify. Without
    this the item stores indistinguishable from a #125-verified capture,
    `briefing._mark` renders it verbatim, `scoring.trust_ceiling` grants the
    full escalation band, and carry's #22 freeze prefers it over a genuinely
    extracted twin at the next rotation.

    The quote itself survives as a claim, exactly like the item's text — it
    just buys nothing until a real serialize re-extracts and verifies it.
    Never call this on the serialize path: there verify_quotes IS the gate,
    and it downgrades misses individually instead of wholesale.
    """
    downgraded = 0
    for item in iter_items(checkpoint):
        if item.get("trust") == "verbatim":
            item["trust"] = "inferred"
            downgraded += 1
    if downgraded:
        log.info("introspection path: %d unverifiable verbatim item(s) "
                 "downgraded to inferred (#511)", downgraded)
    return downgraded


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

    `llm_model_served` (#458 / scar 0032): `llm_model` is the REQUESTED
    alias — routing config. Behind a gateway with silent fallback chains a
    different model can serve the call with no error, so the response body's
    own `model` field is the only per-call truth; llm.py collects the
    distinct names across this process's calls (chunks + merge) and this
    stamp records them: one distinct name -> string, more than one -> sorted
    list AND the substitution-during-run signal (WARNING + the
    `serialize:model-substituted` usage counter). Mixed-within-a-run is the
    ONLY detectable mismatch: alias->served-name mapping is unknowable
    client-side (the served id is provider-prefixed or fully qualified, so a
    raw requested != served compare would always fire). The command backend
    exposes no served-model info and records nothing, so its checkpoints
    carry NO key — honest absence, never the requested name copied over.

    Fail-open: called right before the checkpoint is handed off to
    store/write, so a resolver exception must never fail an otherwise-
    successful serialize. Both fields are simply left absent.
    """
    # #458: a model-authored `llm_model_served` in the extracted JSON is a
    # self-issued receipt — pop unconditionally BEFORE any fail-open early
    # return, so even a stamp that bails leaves honest absence, not a spoof
    # (same discipline as strip_code_owned_keys, #292, but local: this key's
    # legitimate value is only ever computed right here).
    checkpoint.pop("llm_model_served", None)
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
    try:
        served = llm.served_models()
    except Exception:
        served = []
    if not served:
        return
    checkpoint["llm_model_served"] = served[0] if len(served) == 1 else served
    if len(served) > 1:
        log.warning(
            "serialize: model substitution during run — %d distinct served "
            "models %s behind requested %r; every item in this checkpoint "
            "was extracted by at least one model that is not the configured "
            "one (#458)",
            len(served), served, model)
        # Count through the SAME local usage-counter mechanism the cli
        # commands use (#54) — mcp_tools.py already records via
        # cli._note_usage from outside cli, so this matches the existing
        # architecture rather than inventing a counter subsystem. Lazy
        # import keeps the module graph acyclic (cli imports this module);
        # best-effort keeps the stamp fail-open.
        try:
            from . import cli
            cli._note_usage("serialize:model-substituted")
        except Exception:
            pass


def serialize_strict(session_id: str, messages, chat=None, deadline=None,
                     escalate=False) -> dict:
    """Transcript -> validated checkpoint, or a named SerializeError.

    `chat` is an injectable callable (messages, **kwargs) -> str; defaults to the
    real LLM client. `deadline` (time.monotonic() seconds) is the caller's
    budget for ONE wave of LLM work; chunked serializes scale it by the wave
    plan (#314: chunk batches + merge levels) before forwarding to the client,
    so the merge never starts starved by construction.

    Rendered transcripts over DAIMON_CHUNK_LINES go chunked (armC): per-chunk
    D-007 serialize -> 01c merge -> validate. Shorter ones stay single-pass.

    `escalate` (#360, heal-path only): run stage 1 as one extraction pass per
    ESCALATION_PERSPECTIVES entry over every chunk (distinct prompts, own
    #48 cache lanes), stage 2 as the ordinary merge, then the unchanged
    deterministic gates. The wave plan counts every perspective pass, so the
    deadline scales with the real call count (#314 machinery, no new budget).
    Default False keeps this function byte-identical to today.
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
    # #342: first liveness stamp — from here on the child proves it is alive
    # via heartbeats (entry, every chunk/pass, every merge group), so hung
    # detection can trust freshness over total wall-clock. After the
    # too-short gate: a skipped session writes its result line immediately
    # and needs no liveness trail.
    ledger.touch_heartbeat(session_id)
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise LLMCallError("deadline exhausted before the first LLM call")

    # #577: the extractor reads daimon's own tool output blanked, the same way
    # verification already refuses to read it as a witness. `messages` stays
    # unblanked below — verification, grounding and the [mN] markers all key on
    # the original list.
    transcript_text = _render_transcript(extraction_messages(messages))
    chunks = chunk_transcript(transcript_text, config.chunk_lines(), config.chunk_overlap())

    # #314: DAIMON_TIMEOUT is the field-derived floor for ONE slow call (#284,
    # scar #14) — but a chunked serialize is a PLAN of sequential waves (chunk
    # batches + merge levels). Sharing one single-call budget across the plan
    # guaranteed a starved merge on slow gateways, so scale the total by the
    # wave count. Per-call socket timeouts stay capped at the base budget
    # (llm.py), so no single request grows past gateway ceilings (scar #17).
    # #360: an escalated run makes one call per (perspective x chunk) — the
    # wave plan must count them all, or the merge starts starved exactly the
    # way #314 fixed for chunks. Same machinery, no new budget dimension.
    n_units = len(chunks) * (len(ESCALATION_PERSPECTIVES) if escalate else 1)
    if deadline is not None and n_units > 1:
        waves = _plan_waves(n_units, config.chunk_concurrency(),
                            config.merge_group_size())
        if waves > 1:
            extra = (waves - 1) * config.timeout_seconds()
            deadline += extra
            if escalate:
                log.info("escalated call plan (#360): %d wave(s) — deadline "
                         "extended by %ds", waves, extra)
            else:
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

    def _produce_escalated_partials() -> list:
        # #360 stage 1: one extraction pass per (chunk, perspective). Jobs are
        # CHUNK-MAJOR — a later chunk's passes sit later in the partial list —
        # so MERGE_SYS's chronology rules (4/9, and the oldest-first
        # recent_decisions order the briefing's decision cap depends on,
        # scar #6) still see partials in session order.
        systems = escalation_systems()
        log.info("escalated serialize (#360): %d perspective(s) x %d chunk(s)",
                 len(systems), len(chunks))
        jobs = [(i, chunk_text, name, system)
                for i, chunk_text in enumerate(chunks)
                for name, system in systems]

        def _one_pass(job):
            i, chunk_text, name, system = job
            ledger.touch_heartbeat(session_id)  # #342: alive, working
            # Own #48 cache lane per perspective (the key carries the
            # perspective name, #367): a re-escalation reuses its prior
            # paid-for passes; the default lane is never read or written here.
            key = _chunk_cache_key(chunk_text, lane=name)
            cached = _load_chunk_cache(key)
            if cached is not None:
                log.info("perspective %s: chunk %d/%d reused cached extraction (#48)",
                         name, i + 1, len(chunks))
                return cached
            if len(chunks) == 1:
                body = f"TRANSCRIPT:\n{chunk_text}"
            else:
                body = f"TRANSCRIPT (chunk {i + 1} of {len(chunks)}):\n{chunk_text}"
            t0 = time.monotonic()
            partial = _call_and_parse(
                chat, system,
                f"session_id: {session_id}\n\n{body}",
                deadline, f"perspective {name}, chunk {i + 1} of {len(chunks)}",
            )
            log.info("perspective %s: chunk %d/%d done in %.0fs",
                     name, i + 1, len(chunks), time.monotonic() - t0)
            _save_chunk_cache(key, partial)
            return partial

        workers = min(config.chunk_concurrency(), len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # pool.map preserves input order — chunk-major stays chronological.
            return list(pool.map(_one_pass, jobs))

    def _produce(note: str) -> dict:
        nonlocal partials
        if escalate:
            # #360 stage 2: the ordinary merge combines the perspective
            # reports — a PRODUCER of one candidate checkpoint, never a
            # verifier (scar #10); the deterministic gates below are the only
            # judges. Retry (`note`) re-runs ONLY the merge, like chunked.
            if partials is None:
                partials = _produce_escalated_partials()
            return _merge_partials(chat, session_id, list(partials), deadline,
                                   attempt_note=note)
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
                ledger.touch_heartbeat(session_id)  # #342: alive, working
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
    first_reason = validation_reason(checkpoint)
    if first_reason is not None:
        log.info("checkpoint failed validation: %s — one resample with attempt "
                 "nonce (#118)", first_reason)
        # #553: the rejected attempt has already spent the budget the resample
        # is about to need, so guarantee it one call's worth. max(), never
        # assignment — a caller who granted more keeps it. _produce reads
        # `deadline` from this scope at call time, so rebinding here is what
        # every call underneath it sees.
        if deadline is not None:
            floor = time.monotonic() + config.resample_min_seconds()
            if floor > deadline:
                log.info("resample budget re-armed to %ds (#553)",
                         config.resample_min_seconds())
                deadline = floor
        checkpoint = _produce(_RETRY_NOTE)
        strip_code_owned_keys(checkpoint)
        checkpoint["session_id"] = session_id
    reason = validation_reason(checkpoint)
    if reason is not None:
        # #555: both attempts, because the FIRST reason is usually the
        # interesting one — the resample runs against a nonce'd prompt and can
        # fail a different predicate than the output that triggered it.
        raise SchemaValidationError(
            f"checkpoint failed schema/trust validation twice. "
            f"attempt 1: {first_reason}; attempt 2: {reason}"
        )
    sanitize_importance(checkpoint)
    sanitize_scene(checkpoint)
    # #358: translate cited [mN] markers to host message ids and drop any id
    # the transcript cannot vouch for — BEFORE verification, so verify_quotes
    # only ever sees code-validated bindings. #359: signal pointers (ids of
    # tool-result messages) survive on any item, evidence for outcome claims.
    sig_ids = signal_message_ids(messages)
    sanitize_source_ids(checkpoint, message_id_map(messages), sig_ids)
    # #369: deterministic backstop for constraint pinning — hard-imperative
    # user sentences the model paraphrased away are force-pinned as verbatim
    # items here, AFTER id sanitization (host ids go in directly) and BEFORE
    # verification, so every pin earns quote_verified/last_verified through
    # the same gauntlet as a model-chosen quote.
    pin_imperatives(checkpoint, messages)
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
    # #514: date the extraction. PROMPT_VERSION and EXTRACTION_VERSION are two
    # independent levers; without this stamp a lone EXTRACTION_VERSION bump
    # would leave extraction-incomparable checkpoints whose only visible
    # version field (format_version) looks identical. Stamped HERE, not in
    # store.write_checkpoint: only a path that actually ran the extractor may
    # claim its version — introspection checkpoints stay absent = unknown.
    checkpoint["extraction_version"] = EXTRACTION_VERSION
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
