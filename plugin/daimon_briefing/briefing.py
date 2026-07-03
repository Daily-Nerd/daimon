"""Checkpoint -> 'while you were away' briefing text.

Default rendering is a DETERMINISTIC template over the checkpoint JSON — no LLM call.
Rationale: injection happens on the user's critical path (latency matters), and the
checkpoint is already the trusted extract (D-006); re-narrating via LLM reintroduces
generation risk for zero recall gain. LLM rendering is opt-in via DAIMON_LLM_BRIEFING.

Ordering is load-bearing: external-state items (the user-acted-outside-AI gap) come
FIRST under a 'verify before trusting' marker, then open loops, then decisions, then
beliefs, then uncertainties, then contradictions flagged. Verbatim items are marked
distinctly from inferred ones.
"""

import re
import time

from . import config, llm, scoring

_VERBATIM_MARK = "✓ verbatim"
_INFERRED_MARK = "~ inferred"


def _mark(item) -> str:
    return _VERBATIM_MARK if item.get("trust") == "verbatim" else _INFERRED_MARK


def _line(item) -> str:
    text = item.get("text", "").strip()
    quote = item.get("quote", "").strip()
    base = f'- [{_mark(item)}] {text}'
    if item.get("carried_from"):
        # Epistemic honesty, same philosophy as trust marks: a loop carried
        # from an older session must not read as fresh context (#33 Phase 2).
        base += " [carried]"
    if quote:
        base += f'  — "{quote}"'
    return base


def _nonempty(item) -> bool:
    return bool(item and isinstance(item, dict) and item.get("text", "").strip())


def _overflow_note(dropped: int) -> str | None:
    """Marker text when the briefing capped older decisions, or None. Single source
    for both the plain and rich render paths (DRY + one singular/plural rule)."""
    if dropped <= 0:
        return None
    plural = "s" if dropped != 1 else ""
    return f"(+{dropped} earlier decision{plural} — full history in checkpoint)"


def _by_weight(items, item_type, now):
    """Sort a section by #78 effective weight, heaviest first. sorted() is stable,
    so legacy items (no first_seen / no importance -> equal neutral weights) keep
    their serializer order — pre-D-011 checkpoints render exactly as before."""
    return sorted(items, key=lambda i: scoring.effective_weight(i, item_type, now),
                  reverse=True)


def build(checkpoint, now=None) -> dict | None:
    """Structured briefing sections, or None if nothing is worth surfacing.
    Deterministic — no LLM; `now` is injectable for tests. Sections order by #78
    effective weight EXCEPT recent_decisions, which stay chronological (the
    serializer's CHRONOLOGY contract; the tail-cap below depends on it)."""
    if not checkpoint or not isinstance(checkpoint, dict):
        return None
    if now is None:
        now = time.time()

    wc = checkpoint.get("working_context") or {}
    es = checkpoint.get("epistemic_snapshot") or {}

    open_qs = _by_weight([i for i in (wc.get("open_questions") or []) if _nonempty(i)],
                         "open_question", now)
    decisions = [i for i in (wc.get("recent_decisions") or []) if _nonempty(i)]
    beliefs = _by_weight([i for i in (es.get("strong_beliefs") or []) if _nonempty(i)],
                         "strong_belief", now)
    uncertainties = _by_weight([i for i in (es.get("uncertainties") or []) if _nonempty(i)],
                               "uncertainty", now)
    contradictions = [i for i in (es.get("contradictions_flagged") or []) if _nonempty(i)]
    active = wc.get("active_topic")

    if not (open_qs or decisions or beliefs or uncertainties or contradictions
            or _nonempty(active)):
        return None

    # Cap to the most-recent N decisions (tail — recent_decisions is chronological,
    # oldest→newest, per the serializer's CHRONOLOGY instruction). Render-time only:
    # the checkpoint keeps every decision. 0 = unbounded.
    n = config.max_briefing_decisions()
    kept = decisions[-n:] if n and len(decisions) > n else decisions

    return {
        "external": [i for i in open_qs if i.get("external_state")],
        "open_loops": [i for i in open_qs if not i.get("external_state")],
        "decisions": kept,
        "decisions_overflow": len(decisions) - len(kept),
        "active_topic": active if _nonempty(active) else None,
        "beliefs": beliefs,
        "uncertainties": uncertainties,
        "contradictions": contradictions,
    }


# ---- #79: token budget — section-preserving truncation ----

# A bold-labeled section (**Problem:** / **Root Cause:** / **Fix:** ...) plus
# its immediate continuation line — the load-bearing shape ACB's truncation
# preserved (hierarchical_content_generator:774), without its per-label list:
# any **Label:** counts, so user vocabularies survive too.
_SECTION_RE = re.compile(r"\*\*[^*\n]+:\*\*[^\n]*(?:\n(?![*\s])[^\n]+)?")

_TRUNCATION_MARKER = " …[truncated — full text in checkpoint]"

# When a briefing is over budget, single items longer than this get
# section-preserving truncation before anything is dropped outright.
_ITEM_TRUNCATE_CHARS = 400


def estimate_tokens(text: str) -> int:
    """Honest chars//4 estimate (#79) — no tokenizer dependency, and the error
    margin is fine for a budget whose point is order-of-magnitude control."""
    return len(text) // 4


def truncate_preserving_sections(text: str, max_chars: int) -> str:
    """Cut `text` to max_chars, keeping **Label:** sections over filler: if the
    labeled sections alone fit, they ARE the truncation; only a section-less
    text falls back to a blind head-cut. Always appends a visible marker —
    silent truncation reads as 'this is everything' when it isn't."""
    if len(text) <= max_chars:
        return text
    parts = _SECTION_RE.findall(text)
    if parts:
        key = "\n".join(parts)
        if len(key) + len(_TRUNCATION_MARKER) <= max_chars:
            return key + _TRUNCATION_MARKER
    return text[:max(0, max_chars - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER


def _trim_note(dropped: int) -> str:
    plural = "s" if dropped != 1 else ""
    return f"  (+{dropped} item{plural} trimmed for budget — full history in checkpoint)"


# Budget drop order (#79): background sections go before actionable ones, and
# within a section the LOWEST-weight items go first — beliefs/uncertainties are
# #78-sorted heaviest-first, so their tail is the lightest; decisions are
# chronological, so their head is the oldest. external / active_topic /
# contradictions are never dropped: they are the skeleton.
_DROP_ORDER = (("beliefs", "tail"), ("uncertainties", "tail"),
               ("decisions", "head"), ("open_loops", "tail"))


def render_plain(b: dict) -> str:
    """The deterministic briefing text. Under the #79 budget this is
    BYTE-IDENTICAL to the legacy render(); over it, long items truncate
    (sections preserved) and then whole items drop, lowest value first,
    each cut announced with a trim note."""
    budget = config.brief_max_tokens()
    text = _render_parts(b, {})
    if not budget or estimate_tokens(text) <= budget:
        return text

    # Stage 1: shorten monster items in place of dropping them.
    b = dict(b)
    for key, _end in _DROP_ORDER:
        b[key] = [
            {**i, "text": truncate_preserving_sections(
                i.get("text", ""), _ITEM_TRUNCATE_CHARS)}
            for i in (b.get(key) or [])
        ]
    trimmed = {key: 0 for key, _ in _DROP_ORDER}
    text = _render_parts(b, trimmed)

    # Stage 2: drop whole items, least valuable first, until the budget holds
    # or only the skeleton remains.
    for key, end in _DROP_ORDER:
        while estimate_tokens(text) > budget and b.get(key):
            items = list(b[key])
            items.pop(-1 if end == "tail" else 0)
            b[key] = items
            trimmed[key] += 1
            text = _render_parts(b, trimmed)
        if estimate_tokens(text) <= budget:
            break
    return text


def _render_parts(b: dict, trimmed: dict) -> str:
    parts = ["While you were away — here's where we left off."]

    def _section(header: str, key: str) -> None:
        items = b.get(key) or []
        note = trimmed.get(key, 0)
        if not items and not note:
            return
        parts.append("")
        parts.append(header)
        parts.extend(_line(i) for i in items)
        if key == "decisions":
            overflow = _overflow_note(b.get("decisions_overflow", 0))
            if overflow:
                parts.append(f"  {overflow}")
        if note:
            parts.append(_trim_note(note))

    if b["external"]:
        parts.append("")
        parts.append("VERIFY BEFORE TRUSTING (state may have changed outside this session):")
        parts.extend(_line(i) for i in b["external"])

    _section("Open loops:", "open_loops")
    _section("Decisions made:", "decisions")

    if b["active_topic"]:
        parts.append("")
        parts.append(f'Active topic: {b["active_topic"].get("text", "").strip()}')

    _section("Beliefs held:", "beliefs")
    _section("Was uncertain about:", "uncertainties")

    # .get(): hand-built b dicts predating #101 may lack the key (defensive,
    # same spirit as decisions_overflow).
    if b.get("contradictions"):
        parts.append("")
        parts.append("Contradictions flagged:")
        parts.extend(_line(i) for i in b["contradictions"])

    return "\n".join(parts)


def render(checkpoint) -> str | None:
    """Render the briefing, or None if there is nothing worth surfacing.
    LLM rendering is opt-in (DAIMON_LLM_BRIEFING) and falls back to deterministic."""
    b = build(checkpoint)
    if b is None:
        return None
    if config.llm_briefing():
        rendered = _render_llm(checkpoint)
        if rendered:
            return rendered
    return render_plain(b)


# Seeded from research/experiments/track-a/prompts/02-reconstruct.md, tuned for a
# skimmable briefing rather than a two-part reconstruction.
_RECONSTRUCT_SYS = """You are resuming a work session. Your only memory of the previous session is the cognitive checkpoint below. You do NOT have the original transcript.

Write a <30-second, skimmable "while you were away / here's where we left off" briefing.
ORDER IT: items flagged external_state FIRST under a clear "verify before trusting" heading
(their state may have changed outside the session); then open loops; then decisions; then beliefs;
then any contradictions_flagged (as their own "contradictions flagged" section — omit it when empty).
Mark each item as verbatim or inferred.

CRITICAL: base every claim ONLY on the checkpoint. Do NOT add plausible-sounding detail that is
not in the checkpoint. If the checkpoint is thin, the briefing should be thin. Do not embellish."""


def _render_llm(checkpoint) -> str | None:
    import json

    try:
        return llm.chat(
            [
                {"role": "system", "content": _RECONSTRUCT_SYS},
                {"role": "user", "content": "CHECKPOINT:\n" + json.dumps(checkpoint, indent=2)},
            ],
            # temperature comes from config (default 0.0 for determinism;
            # DAIMON_LLM_TEMPERATURE overrides).
        )
    except Exception:
        return None
