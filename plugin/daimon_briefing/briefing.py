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

import copy
import logging
import re
import time

# store/carry import graph checked (#103): neither store, carry, recall,
# scoring, nor serializer imports briefing — no cycle, so this stays a normal
# module-level import (contrast carry.py's own local-import notes, which
# don't apply here).
from . import carry, config, llm, receipts, schema, scoring, serializer, store

log = logging.getLogger("daimon.briefing")

_VERBATIM_MARK = "✓ verbatim"
_INFERRED_MARK = "~ inferred"
_UNTAGGED_MARK = "? untagged"
# #204: when a receipt-era checkpoint's provenance can't be locally confirmed at
# brief time, a `verbatim` label has NOT earned its checkmark — the stored bytes
# may have been edited. Degrade it visibly rather than assert integrity we can't
# prove. Inferred/untagged never claimed integrity, so they never degrade.
_DEGRADED_MARK = "⚠ unverified (verbatim)"
DEGRADE_NOTE = (
    "⚠ RECEIPT UNVERIFIED — this checkpoint claims signed provenance, but its "
    "receipt is missing or no longer matches the stored bytes. The 'verbatim' "
    "quotes below are shown UNVERIFIED (run `daimon verify-receipt`).")


def receipt_degraded(checkpoint) -> bool:
    """Cheap brief-time provenance check (#204), fail-open. Delegates to
    receipts.verbatim_degraded — sidecar presence + outputs_hash byte match only,
    never the vitni CLI (full crypto is `daimon verify-receipt`)."""
    try:
        return receipts.verbatim_degraded(checkpoint)
    except Exception:
        return False


def _mark(item, degraded: bool = False) -> str:
    # A missing/empty trust class renders as "untagged", never as a confident
    # "inferred" the item never earned (#30) — the recall CLI already agrees.
    trust = item.get("trust")
    if trust == "verbatim":
        return _DEGRADED_MARK if degraded else _VERBATIM_MARK
    if trust:
        return _INFERRED_MARK
    return _UNTAGGED_MARK


def _line(item, degraded: bool = False) -> str:
    # #134: dict.get returns the stored None for a present-but-null key (the
    # default only fires for an ABSENT key), so a torn/legacy checkpoint could
    # crash the whole render here. Use the codebase's str(x or "") idiom
    # (store.py, carry.py) — tolerant of null, same as iter_items' stance.
    text = str(item.get("text") or "").strip()
    quote = str(item.get("quote") or "").strip()
    base = f'- [{_mark(item, degraded)}] {text}'
    if item.get("carried_from"):
        # Epistemic honesty, same philosophy as trust marks: a loop carried
        # from an older session must not read as fresh context (#33 Phase 2).
        base += " [carried]"
    if quote:
        base += f'  — "{quote}"'
    candidate = item.get("_supersede_candidate")
    if candidate:
        # #14: a machine-suggested (unconfirmed) supersession — never
        # withheld, just flagged with a one-command confirm path.
        item_id = item.get("id") or "?"
        base += (f"\n  ⚠ likely superseded by {candidate} — confirm: "
                 f"daimon resolve {item_id} --status superseded-by:{candidate}"
                 f"\n    reject: daimon reverify {item_id}")
    wc = item.get("_worldcheck")
    if isinstance(wc, dict) and wc.get("note"):
        # #365: worldcheck contradiction — the world moved off-session. Same
        # philosophy as the #14 candidate flag above (a machine observation
        # is surfaced, never suppressed), reusing the same resolve/reverify
        # confirm/reject command surface. The note/status vocabulary is
        # bounded at the stamp site (worldcheck._KNOWN_STATES), so nothing
        # free-form rides into this line. ADDED lines only — the pinned
        # prefix above never changes.
        base += f"\n  ⚠ state changed since capture: {wc['note']}"
        item_id = item.get("id")
        if item_id:
            # Confirming writes a human resolution event (source=cli), which
            # withholds the item from future briefs; rejecting keeps it live.
            base += (f" — confirm: daimon resolve {item_id} "
                     f"--status {wc.get('status') or 'resolved'}"
                     f"\n    reject: daimon reverify {item_id}")
    return base


def _nonempty(item) -> bool:
    # #134: null-safe — a present-but-null text must read as empty, not crash.
    return bool(item and isinstance(item, dict) and str(item.get("text") or "").strip())


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


# ---- #103: withhold event-resolved items at render time ----

# #14 shape gate for a supersede-candidate's new-id payload: kind initial +
# hex slice (+ optional collision counter), same shape store._stamp_item_ids
# emits and carry._ID_SHAPE recognizes — duplicated rather than imported
# because carry's copy is unbounded ({6,}) and this one fullmatches
# attacker-adjacent event text, where bounded quantifiers are the rule.
# Also gates the fuzzy pool (#145): a resolution ref of this shape belongs
# to a stamped item, whose suppression is exact-id-only.
_CANDIDATE_ID_SHAPE = re.compile(r"[a-z]-[0-9a-f]{6,40}(-\d+)?")


def withhold(checkpoint, resolutions: dict) -> tuple[dict, list, list]:
    """Drop items the world has already resolved, at RENDER time only — the
    checkpoint on disk (and carry's copy of it) is never touched. `resolutions`
    is `{item_ref: latest_event}`, exactly store.resolutions()'s shape; pure,
    no I/O — the caller does the read (fail-open lives there, not here).

    Binding is exact for id-bearing items: an item withholds only if ITS OWN
    id is a resolved ref. id-LESS (legacy) items fall back to a fuzzy match on
    item_text via carry._same_item/_generic_terms — but that fuzzy path is
    id-bearing items' one guardrail: they NEVER take it, even on an exact text
    coincidence (test_id_bearing_item_never_fuzzy_withheld). A fuzzy withhold
    of an id-bearing item would silently suppress a live memory that merely
    resembles a closed one — the worst failure mode this feature can have.
    The pool is guarded symmetrically (#145): only resolutions whose OWN ref
    is not id-shaped feed the fuzzy match, so a resolved id-bearing loop's
    text can't fuzzy-suppress a live id-less item that merely resembles it.

    #14: a THIRD outcome — a "supersede-candidate:<new-id>" latest event is a
    machine SUGGESTION, not a resolution (store.is_resolved says so: it stays
    live). Candidates are never dropped; instead the RETURNED COPY's item gets
    a transient `_supersede_candidate = "<new-id>"` stamp so render/CLI layers
    can flag it — id-bearing only, by construction (candidates are only ever
    emitted against ids).

    No resolved/candidate events, or a non-dict checkpoint ->
    (checkpoint, [], []) UNCHANGED, same no-op idiom as carry.merge: no copy
    is made unless something actually withholds or is stamped, so the common
    case (nothing resolved yet) costs nothing."""
    if not isinstance(checkpoint, dict) or not resolutions:
        return checkpoint, [], []

    resolved_refs = {ref for ref, evt in resolutions.items() if store.is_resolved(evt)}
    candidate_refs: dict[str, str] = {}
    for ref, evt in resolutions.items():
        if not isinstance(evt, dict):
            continue
        status = str(evt.get("status") or "")
        if status.lower().startswith("supersede-candidate") and ":" in status:
            new_id = status.split(":", 1)[1].strip()
            # Shape gate: the status field is free-form by design, so the
            # payload after the colon can be ANY text — and it rides verbatim
            # into the rendered confirm-command suggestion and the hook-
            # injected LLM context (an injection surface). Only an id-shaped
            # payload earns a stamp; a malformed machine claim earns no
            # surface at all (unannotated, unlisted — still never withheld).
            # Mirrors carry._ID_SHAPE, with the hex run bounded (fullmatch on
            # attacker-adjacent input wants bounded quantifiers).
            if new_id and _CANDIDATE_ID_SHAPE.fullmatch(new_id):
                candidate_refs[ref] = new_id

    if not resolved_refs and not candidate_refs:
        return checkpoint, [], []
    # #145: the fuzzy pool holds ONLY resolutions whose own ref is not
    # id-shaped (legacy, pre-id-stamping events). An id-bearing resolution is
    # fully handled by the exact id branch below — its text in this pool
    # contributes nothing to correct suppression and only creates the false-
    # positive surface where a live id-less item that merely RESEMBLES an
    # unrelated closed loop gets silently withheld. Ref shape decides:
    # store._stamp_item_ids only ever emits ids of this shape, so a
    # non-matching ref cannot belong to a stamped item. When the shape read
    # is wrong the item is shown, not withheld — fail-open.
    fuzzy_refs = [ref for ref in resolved_refs
                  if not _CANDIDATE_ID_SHAPE.fullmatch(str(ref))]
    resolved_texts = [str(resolutions[ref].get("item_text") or "").strip()
                       for ref in fuzzy_refs]
    resolved_texts = [t for t in resolved_texts if t]

    # Dry run over the ORIGINAL checkpoint — decide what would be withheld/
    # stamped before paying for a deepcopy (most briefs resolve nothing).
    to_drop = []  # [(section, key, index, item, event)]
    to_stamp = []  # [(section, key, index, event, new_id)]
    for section, key in store._ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if item_id:
                # resolved_refs is built from resolutions.items(), so membership
                # here already guarantees resolutions[item_id] exists (M1: the
                # old `evt is not None and ...` check was redundant — a subset
                # check never needs the superset's own membership re-verified).
                if item_id in resolved_refs:
                    to_drop.append((section, key, idx, item, resolutions[item_id]))
                elif item_id in candidate_refs:
                    to_stamp.append((section, key, idx, resolutions[item_id],
                                      candidate_refs[item_id]))
                continue  # id-bearing: bound exactly or not at all, never fuzzy
            text = str(item.get("text") or "").strip()
            if not text or not resolved_texts:
                continue
            generic = carry._generic_terms(resolved_texts + [text])
            for ref in fuzzy_refs:
                evt = resolutions[ref]
                cand_text = str(evt.get("item_text") or "").strip()
                if cand_text and carry._same_item(text, cand_text, generic):
                    to_drop.append((section, key, idx, item, evt))
                    break

    if not to_drop and not to_stamp:
        return checkpoint, [], []

    out = copy.deepcopy(checkpoint)

    # Stamp BEFORE dropping: to_stamp/to_drop indices both refer to the
    # ORIGINAL (pre-removal) list positions, and stamping never changes list
    # length — so stamping first keeps every index valid for the drop pass
    # that follows, regardless of whether a stamped and a dropped item share
    # a section/key list.
    candidates = []
    for section, key, idx, evt, new_id in to_stamp:
        item = out[section][key][idx]
        item["_supersede_candidate"] = new_id
        candidates.append((key, item, evt))

    withheld = []
    drop_idx_by_list: dict[tuple[str, str], set] = {}
    for section, key, idx, item, evt in to_drop:
        drop_idx_by_list.setdefault((section, key), set()).add(idx)
        withheld.append((key, item, evt))
    for (section, key), idxs in drop_idx_by_list.items():
        items = out[section][key]
        kept = [it for i, it in enumerate(items) if i not in idxs]
        items[:] = kept

    return out, withheld, candidates


# ---- #215: staleness budget — carried items nobody has world-checked ----


def stale_carried(checkpoint, resolutions: dict, now, threshold_days=None) -> list:
    """Carried items whose EFFECTIVE last-verified age exceeds
    `threshold_days`, or [] if none. Pure — `now` is injected (mirrors
    cli._status_health's purity), no I/O; the caller does resolutions'
    read (store.resolutions(), same shape `withhold` already consumes) and
    passes it in.

    Why this exists: a carried item survives into a fresh checkpoint by
    exact-copy (carry.merge), and the fresh checkpoint restating it is
    NOT corroboration — both sources trace back to the same original
    extraction. This is the render-time signal that a carried claim has
    ridden along for a while with nobody actually re-checking it against the
    world (code, git, issue tracker).

    A candidate must carry `carried_from` (native, this-session items were
    just re-extracted — not in question). Its EFFECTIVE last-verified is the
    NEWEST of, when parseable:
      - `last_verified` (#215/#125: stamped by verify_quotes at serialize
        time, ONLY there — checkpoints are append-only, so this field is
        never rewritten by carry or by a resolve/reverify action)
      - the latest events.jsonl event's `ts` for the item's id, from
        `resolutions` (store.resolutions()'s {item_ref: latest_event} shape
        — the read-time fold of `daimon resolve`/`reverify`, which is where a
        user's real world-check moment lands; carry never touches the
        checkpoint for it, per #215's design constraint)
      - `first_seen` (birth stamp, the oldest and least informative fallback)

    Timestamps are parsed via store._created_epoch, which returns None on
    anything torn/legacy/malformed — fail-open: an unparseable candidate
    contributes NOTHING to the age (never itself the reason for a false
    alarm), and an item where EVERY candidate is unparseable is not counted
    stale at all (house rule: no evidence beats a false no-line guarantee,
    same as _status_health's no-age-threshold-without-data stance)."""
    if threshold_days is None:
        threshold_days = config.stale_days()
    if not isinstance(checkpoint, dict):
        return []
    resolutions = resolutions if isinstance(resolutions, dict) else {}
    stale = []
    for item in serializer.iter_items(checkpoint):
        if not isinstance(item, dict) or not item.get("carried_from"):
            continue
        candidates = []
        lv = store._created_epoch(item.get("last_verified"))
        if lv is not None:
            candidates.append(lv)
        evt = resolutions.get(item.get("id"))
        if isinstance(evt, dict):
            evt_ts = store._created_epoch(evt.get("ts"))
            if evt_ts is not None:
                candidates.append(evt_ts)
        fs = store._created_epoch(item.get("first_seen"))
        if fs is not None:
            candidates.append(fs)
        if not candidates:
            continue  # no parseable stamp at all — fail open, not stale
        age_days = (now - max(candidates)) / 86400.0
        if age_days > threshold_days:
            stale.append(item)
    return stale


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


def render_plain(b: dict, degraded: bool = False) -> str:
    """The deterministic briefing text. Under the #79 budget this is
    BYTE-IDENTICAL to the legacy render(); over it, long items truncate
    (sections preserved) and then whole items drop, lowest value first,
    each cut announced with a trim note. `degraded` (#204) downgrades every
    verbatim label and adds one header note when the receipt is unverifiable."""
    budget = config.brief_max_tokens()
    text = _render_parts(b, {}, degraded)
    if not budget or estimate_tokens(text) <= budget:
        return text

    # Stage 1: shorten monster items in place of dropping them. Verbatim text
    # is exempt (#30) — the #23 freeze made it immutable in carry, and a
    # render that rewrites it under budget pressure breaks the same guarantee.
    # An oversized verbatim item can still be DROPPED whole in stage 2
    # (announced by the trim note); it is never rewritten.
    b = dict(b)
    for key, _end in _DROP_ORDER:
        b[key] = [
            i if i.get("trust") == "verbatim"
            else {**i, "text": truncate_preserving_sections(
                i.get("text", ""), _ITEM_TRUNCATE_CHARS)}
            for i in (b.get(key) or [])
        ]
    trimmed = {key: 0 for key, _ in _DROP_ORDER}
    text = _render_parts(b, trimmed, degraded)

    # Stage 2: drop whole items, least valuable first, until the budget holds
    # or only the skeleton remains.
    for key, end in _DROP_ORDER:
        while estimate_tokens(text) > budget and b.get(key):
            items = list(b[key])
            items.pop(-1 if end == "tail" else 0)
            b[key] = items
            trimmed[key] += 1
            text = _render_parts(b, trimmed, degraded)
        if estimate_tokens(text) <= budget:
            break
    return text


def _render_parts(b: dict, trimmed: dict, degraded: bool = False) -> str:
    parts = ["While you were away — here's where we left off."]
    if degraded:
        # One header note (#204), embedded in the text so the hook-injected
        # briefing carries it too — not just the human-facing CLI render.
        parts.append("")
        parts.append(DEGRADE_NOTE)

    def _section(header: str, key: str) -> None:
        items = b.get(key) or []
        note = trimmed.get(key, 0)
        if not items and not note:
            return
        parts.append("")
        parts.append(header)
        parts.extend(_line(i, degraded) for i in items)
        if key == "decisions":
            overflow = _overflow_note(b.get("decisions_overflow", 0))
            if overflow:
                parts.append(f"  {overflow}")
        if note:
            parts.append(_trim_note(note))

    if b["external"]:
        parts.append("")
        parts.append("VERIFY BEFORE TRUSTING (state may have changed outside this session):")
        parts.extend(_line(i, degraded) for i in b["external"])

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
        parts.extend(_line(i, degraded) for i in b["contradictions"])

    return "\n".join(parts)


def _iter_trusted_quotes(checkpoint):
    """Yield every verbatim item's quote across the cognitive sections.
    Sections come from schema.ITEM_FIELDS so a field added there is validated
    here without another hand-kept list (#146 drift class; #161 added the
    active_topic singleton this way)."""
    for field in schema.ITEM_FIELDS:
        value = (checkpoint.get(field.section) or {}).get(field.key)
        for item in (value,) if field.singleton else (value or []):
            if (isinstance(item, dict) and item.get("trust") == "verbatim"
                    and str(item.get("quote") or "").strip()):
                yield str(item["quote"]).strip()


def _validate_llm_render(rendered: str, checkpoint) -> bool:
    """The mechanical check the deterministic render gets for free (#30): every
    verbatim quote must survive the LLM's prose INTACT. Whitespace-normalized
    on both sides — LLMs re-wrap lines, and a re-wrapped quote is still the
    exact wording. Any lost or mutated quote fails the whole render; the
    verbatim/inferred distinction is a guarantee, not a request."""
    haystack = re.sub(r"\s+", " ", rendered)
    for quote in _iter_trusted_quotes(checkpoint):
        if re.sub(r"\s+", " ", quote) not in haystack:
            return False
    return True


def render(checkpoint) -> str | None:
    """Render the briefing, or None if there is nothing worth surfacing.
    LLM rendering is opt-in (DAIMON_LLM_BRIEFING), post-validated for verbatim
    quote integrity, and falls back to deterministic on any doubt."""
    b = build(checkpoint)
    if b is None:
        return None
    degraded = receipt_degraded(checkpoint)
    if config.llm_briefing():
        rendered = _render_llm(checkpoint)
        if rendered:
            if _validate_llm_render(rendered, checkpoint):
                # The LLM render carries no per-item marks to degrade; prepend the
                # one header note so an unverifiable receipt still fails loud (#204).
                return f"{DEGRADE_NOTE}\n\n{rendered}" if degraded else rendered
            log.warning("llm briefing dropped a verbatim quote — "
                        "falling back to the deterministic render")
    return render_plain(b, degraded)


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
