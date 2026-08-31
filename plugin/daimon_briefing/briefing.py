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
from . import (capture, carry, config, llm, receipts, refutations, requests,
               schema, scoring, serializer, store)
# Imported as constants, not as the module: withhold()'s `amendments`
# parameter (the public keyword every caller uses) would shadow the module
# name inside that function.
from .amendments import CHANGES as _AMEND_CHANGES
from .amendments import RENDER_STATES as _AMEND_RENDER_STATES

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
# #423: a teammate's `verbatim` claim cannot be verified on this machine —
# receipts resolve against the LOCAL checkpoint dir — so the inbound gate
# clamps it to inferred and marks it; render states BOTH facts visibly.
FOREIGN_VERBATIM_NOTE = "[teammate claims verbatim — unverifiable here]"


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


# #268: how many independent sightings a claim needs before the render says
# so. The origin of record is the first, so ONE corroborating session clears
# the bar — and a lone unwitnessed claim stays silent rather than announcing
# "×1", which would read as evidence where there is none.
CORROBORATION_MIN = 2


def corroboration_badge(item) -> str:
    """The ` [≈ corroborated ×N]` annotation for an item, or "" (#268 slice 4).

    A SEPARATE axis from the trust class — `_mark` says what KIND of evidence
    backs the claim, this says how many independent sessions have witnessed
    it. A corroborated inferred item is still inferred.

    Four suppressions, one rule: a pending machine claim or a contradiction
    never co-renders with a well-witnessed badge — the amend axis joins on
    the same footing as the #480 agent claim (both are agent-initiated
    assertions the witness count would appear to endorse).

    The original two, same rule: a contradiction never co-renders with a
    well-witnessed badge. An item flagged as likely superseded (#14) or
    contradicted by the world (#365) shows the contradiction ALONE — a witness
    count printed beside "this is probably wrong" reads as support for the
    claim, inverting the very signal corroboration exists to carry. Silence
    costs a boost; the inversion costs the axis.

    One literal, shared by the plain path (_line) and the rich panel
    (render._rich_brief), so the two can never drift."""
    n = item.get("_corroborated")
    if not isinstance(n, int) or n < CORROBORATION_MIN:
        return ""
    if (item.get("_supersede_candidate") or item.get("_worldcheck")
            or item.get("_agent_claim") or item.get("_amend")):
        return ""
    return f" [≈ corroborated ×{n}]"


# ---- #480 slice 4: the pending agent-claim flavor — never withheld, rendered ----

# Evidence quotes can be arbitrarily long (they are copy-pasted transcript
# spans); the brief line is meant to be skimmable, not a full transcript
# replay — the checkpoint keeps the full text, this is a display cap only.
_AGENT_CLAIM_EVIDENCE_CHARS = 120


def _truncate_agent_claim(evidence: str | None) -> str:
    # The annotation was the lie, not the callers (#842). Every call site
    # feeds this a `.get()` result, and the body has always coped with a
    # missing one through `or ""` — an absent claim renders as empty, which is
    # the behavior three render paths depend on. Declaring `str` described a
    # contract the function never enforced and no caller ever kept.
    text = str(evidence or "").strip()
    if len(text) <= _AGENT_CLAIM_EVIDENCE_CHARS:
        return text
    return text[:_AGENT_CLAIM_EVIDENCE_CHARS].rstrip() + "…"


# ---- #480 slice 1: resolve handles on open-loop-class items ----

# build()'s section keys that render a resolve handle — the single source of
# truth both render paths (plain _line below, rich render._rich_brief) and
# `daimon loops` key off of. Decisions/beliefs/contradictions are valid
# `daimon resolve` targets too (resolve accepts any item id), but are not
# loop-shaped: stamping a handle there would invite resolving settled facts,
# which is out of this slice's scope.
BRIEFABLE_SECTIONS = frozenset({"external", "open_loops", "uncertainties"})

# The raw checkpoint field keys underlying BRIEFABLE_SECTIONS above — build()
# splits ONE field (open_questions) into "external"/"open_loops" by the
# external_state flag, so the raw-checkpoint view collapses back to two keys.
# `daimon loops` walks store._ITEM_LISTS (raw section/key pairs), not the
# built briefing dict, so it needs this mapping rather than BRIEFABLE_SECTIONS
# itself.
BRIEFABLE_ITEM_KEYS = frozenset({"open_questions", "uncertainties"})


def _handle_suffix(item, briefable: bool) -> str:
    """The compact ` [id]` handle appended to a briefable item's rendered
    line — the read side of the #480 write path: an agent (or a human via
    `daimon loops`) needs something to pass to `daimon resolve`. A legacy
    item with no id renders unchanged (empty string), and a non-briefable
    item (decision/belief/contradiction) never earns one in this slice
    regardless of whether it happens to carry an id."""
    if not briefable:
        return ""
    item_id = item.get("id")
    return f" [{item_id}]" if item_id else ""


def _line(item, degraded: bool = False, briefable: bool = False) -> str:
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
    if item.get("foreign_verbatim_claim"):
        # #423: the inbound gate clamped a teammate's verbatim claim to
        # inferred; state both facts — claimed verbatim, unverifiable here.
        base += f" {FOREIGN_VERBATIM_NOTE}"
    base += corroboration_badge(item)
    because = str(item.get("because") or "").strip()
    if because:
        # F4 (#527): the decision travels with its stated reasoning — a
        # decision whose why got compacted away invites re-litigation.
        base += f" — because {because}"
    if item.get("_worldcheck_confirmed") and not item.get("_worldcheck"):
        # #525: trusted ground — worldcheck agreed with this claim during
        # THIS brief. A separate axis from the trust class (how it was
        # captured) and from corroboration (how many sessions witnessed it):
        # this says the world itself just agreed. A contradiction on any
        # other axis suppresses it — quicksand outranks ground.
        base += " [✓ world-checked]"
    if quote:
        base += f'  — "{quote}"'
    base += _handle_suffix(item, briefable)
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
    claim = item.get("_agent_claim")
    if claim:
        # #480 slice 4: a still-pending agent resolve candidate (#480 slice
        # 2/3) — same never-withheld, always-flagged philosophy as the #14/
        # #365 blocks above, its own confirm/reject pair. ADDED lines only;
        # the pinned prefix above never changes.
        item_id = item.get("id") or "?"
        base += (f'\n  ⚠ agent claims resolved — unverified: '
                 f'"{_truncate_agent_claim(claim)}"'
                 f"\n    confirm: daimon resolve {item_id} --status resolved"
                 f"\n    reject: daimon reverify {item_id}")
    amends = item.get("_amend")
    if isinstance(amends, dict):
        # #691: the item stays open; its state advanced. Two frames, decided
        # by who confirmed: a HUMAN-ratified amendment renders as settled
        # (`↷ amended`), still naming an agent proposer; a merely quote-
        # VERIFIED one renders as a flagged, agent-attributed, UNCONFIRMED
        # claim with the #14/#365/#480 confirm/reject shape — the byte-check
        # certifies transcription, not truth, and an agent can manufacture
        # the quote by speaking it. Every part is re-bounded at the stamp
        # site (withhold): closed change vocab, clipped role, truncated
        # quote/note. ADDED lines only; the pinned prefix never changes.
        rows = amends.get("rows")
        for amend in rows if isinstance(rows, list) else []:
            if not isinstance(amend, dict):
                continue
            change = str(amend.get("change") or "")
            quote = _truncate_agent_claim(amend.get("quote"))
            a_id = amend.get("id") or "?"
            if amend.get("state") == "ratified":
                by = ", agent-proposed" if amend.get("by") == "agent" else ""
                base += (f'\n  ↷ amended — {change} '
                         f'({amend.get("label")}{by}): "{quote}"')
                note = str(amend.get("note") or "").strip()
                if note:
                    base += f"\n    note: {_truncate_agent_claim(note)}"
            else:
                role = str(amend.get("role") or "").strip()
                base += (f'\n  ⚠ agent-proposed amendment — {change} '
                         f'(quote-verified, role: {role}), unconfirmed: '
                         f'"{quote}"'
                         f"\n    confirm: daimon amend ratify {a_id}"
                         f"\n    reject: daimon amend reject {a_id}")
        overflow = amends.get("overflow")
        if isinstance(overflow, int) and overflow > 0:
            base += (f"\n    … {overflow} earlier amendment(s) — "
                     f"daimon amend list")
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


def injection_read_route(project) -> "store.Route":
    """The ONE display-policy route shared by the two briefing-injection
    surfaces (#784, #795): fall back to the global pointer only when the
    project is unknown — nothing is foreign to a session with no project
    identity — or the operator opted in. The `project is None` term is live
    on the hook path (resolve_project_root propagates None) and dead on the
    CLI path (_resolve_project always returns a str); folding them is safe,
    citing the duplication as a shared cause is not. Display callers only:
    the persist path (store.write_checkpoint) must never consult this, or an
    env var could change what carry writes (#126)."""
    if project is None or config.brief_global_fallback():
        return store.Route.OWN_ELSE_GLOBAL
    return store.Route.OWN


def withhold(checkpoint: dict, resolutions: dict,
             amendments=None) -> tuple[dict, list, list]:
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

    #480 slice 4: a FOURTH outcome — a still-pending agent resolve candidate
    (#480 slice 2/3: latest event is `resolving-candidate`, source="agent",
    not yet confirmed by serialize-time verification or a human). Same
    never-withheld shape as the #14 candidate above, its own transient stamp:
    the RETURNED COPY's item gets `_agent_claim = "<evidence quote>"`. Reuses
    capture._pending_agent_candidates over `resolutions` rather than
    re-deriving the status/source filter here — the fold that decides
    idempotence (a confirmed ref's latest event is no longer
    resolving-candidate) and the human-reopen case stays in exactly one
    place. Kept OUT of the `candidates` return list on purpose: that list is
    #14's own "likely superseded (unconfirmed)" subsection
    (`status --suppressed`), a different machine suggestion with a different
    confirm/reject pair — mixing the two would blur what a human is being
    asked to confirm.

    #691: a FIFTH outcome — `amendments` is amendments.renderable()'s shape
    ({item_id: [folded records]}, verified/ratified ONLY — the ledger's
    renderable() already refuses candidates, so nothing unverified can reach
    a stamp through this argument). The RETURNED COPY's item gets a transient
    `_amend` list of bounded payloads. A separate axis from the resolution
    chain above: an item can carry a supersede candidate AND an amendment.
    An item being withheld keeps its drop — amendments annotate live items
    and die with resolved ones.

    No resolved/candidate/pending-claim events, or a non-dict checkpoint ->
    (checkpoint, [], []) UNCHANGED, same no-op idiom as carry.merge: no copy
    is made unless something actually withholds or is stamped, so the common
    case (nothing resolved yet) costs nothing."""
    if not isinstance(checkpoint, dict) or (not resolutions and not amendments):
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
    agent_claim_refs = capture._pending_agent_candidates(resolutions)
    amend_refs = amendments if isinstance(amendments, dict) else {}

    if (not resolved_refs and not candidate_refs and not agent_claim_refs
            and not amend_refs):
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
    to_stamp_claim = []  # [(section, key, index, evidence)] — #480 slice 4
    to_stamp_amend = []  # [(section, key, index, payloads)] — #691
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
                # A ref can match at most one of these three: resolutions is
                # {ref: LATEST event}, and is_resolved/#14's shape gate/#480's
                # pending-candidate filter are mutually exclusive readings of
                # that one event's status.
                if item_id in resolved_refs:
                    to_drop.append((section, key, idx, item, resolutions[item_id]))
                elif item_id in candidate_refs:
                    to_stamp.append((section, key, idx, resolutions[item_id],
                                      candidate_refs[item_id]))
                elif item_id in agent_claim_refs:
                    to_stamp_claim.append(
                        (section, key, idx, agent_claim_refs[item_id]))
                entry = amend_refs.get(item_id)
                if entry is not None and item_id not in resolved_refs:
                    # #691: bounded payloads only — the change vocabulary and
                    # render state are re-checked HERE, not trusted from the
                    # ledger (a row edited on disk must not ride into the
                    # injected context), the role is clipped, and the quote
                    # is truncated at render. The proposer's authority is
                    # carried so the line can attribute the claim. Skipped
                    # for a withheld item: its annotation dies with it.
                    rows = (entry.get("rows")
                            if isinstance(entry, dict) else entry)
                    payloads = [
                        {"id": str(rec.get("amendment_id") or ""),
                         "change": str(rec.get("change") or ""),
                         "quote": str(rec.get("evidence") or ""),
                         "label": str(rec.get("verdict_label") or ""),
                         "role": str(rec.get("evidence_role") or "")[:32],
                         "state": str(rec.get("state") or ""),
                         "by": str(rec.get("proposed_by") or ""),
                         "note": str(rec.get("note") or "")}
                        for rec in (rows if isinstance(rows, list) else [])
                        if isinstance(rec, dict)
                        and str(rec.get("change") or "") in _AMEND_CHANGES
                        and str(rec.get("state") or "")
                        in _AMEND_RENDER_STATES]
                    overflow = (entry.get("overflow", 0)
                                if isinstance(entry, dict) else 0)
                    if payloads:
                        to_stamp_amend.append(
                            (section, key, idx,
                             {"rows": payloads,
                              "overflow": overflow
                              if isinstance(overflow, int) else 0}))
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

    if (not to_drop and not to_stamp and not to_stamp_claim
            and not to_stamp_amend):
        return checkpoint, [], []

    out = copy.deepcopy(checkpoint)

    # Stamp BEFORE dropping: to_stamp/to_stamp_claim/to_drop indices all refer
    # to the ORIGINAL (pre-removal) list positions, and stamping never changes
    # list length — so stamping first keeps every index valid for the drop
    # pass that follows, regardless of whether a stamped and a dropped item
    # share a section/key list.
    candidates = []
    for section, key, idx, evt, new_id in to_stamp:
        item = out[section][key][idx]
        item["_supersede_candidate"] = new_id
        candidates.append((key, item, evt))
    for section, key, idx, evidence in to_stamp_claim:
        out[section][key][idx]["_agent_claim"] = evidence
    for section, key, idx, payload in to_stamp_amend:
        out[section][key][idx]["_amend"] = payload

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


# ---- #268: corroboration — independent sightings, stamped for the render ----


def mark_corroborated(checkpoint, corroborations: dict):
    """Stamp corroborated items with a transient `_corroborated = N` count and
    return the result; pure, no I/O — the caller does the read, exactly as
    `withhold` takes `store.resolutions()`'s output (#268 slice 4).

    `corroborations` is `store.corroborations()`'s shape, keyed by bare item
    id. N = 1 + the EFFECTIVE origins: the origin of record is the claim's
    first sighting, and every session in `origins` is one more. `recorded` is
    deliberately not counted — a witness discounted by a later contradiction
    stays on the record without paying, and re-deriving that verdict here
    would be a second opinion about a question the fold already answered.
    Below `CORROBORATION_MIN` nothing is stamped at all, so an uncorroborated
    item is byte-identical to its pre-#268 render.

    Transient like withhold's candidate stamps and worldcheck's flags: the
    count lives in events.jsonl, and a `_corroborated` key on a stored
    checkpoint would be a second, forgeable copy of it. Nothing here writes.

    No corroborations, or a non-dict checkpoint -> the input UNCHANGED, same
    no-op idiom as withhold/carry.merge: no copy unless something is actually
    stamped, so the common case (nothing witnessed yet) costs nothing."""
    if not isinstance(checkpoint, dict) or not corroborations:
        return checkpoint

    # Dry run over the ORIGINAL, then one deepcopy — withhold's shape exactly.
    to_stamp = []  # [(section, key, index, n)]
    for section, key in store._ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            entry = corroborations.get(item.get("id"))
            if not isinstance(entry, dict):
                continue
            n = 1 + len(entry.get("origins") or ())
            if n >= CORROBORATION_MIN:
                to_stamp.append((section, key, idx, n))

    if not to_stamp:
        return checkpoint

    out = copy.deepcopy(checkpoint)
    for section, key, idx, n in to_stamp:
        out[section][key][idx]["_corroborated"] = n
    return out


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
    labeled sections alone fit, they ARE the truncation; when they do not fit
    the cut still lands INSIDE them, and only a section-less text falls back to
    a blind head-cut of the raw text. Always appends a visible marker — silent
    truncation reads as 'this is everything' when it isn't.

    #489: the over-budget case used to fall through to the raw head-cut, which
    returned unlabeled preamble and dropped every section it had just found —
    the inverse of the contract, and worst on the longest, most structured
    items. Cutting the joined sections degrades predictably instead: the
    leading label survives, and what is lost is the tail rather than all of it.
    """
    if len(text) <= max_chars:
        return text
    parts = _SECTION_RE.findall(text)
    body = "\n".join(parts) if parts else text
    if parts and len(body) + len(_TRUNCATION_MARKER) <= max_chars:
        return body + _TRUNCATION_MARKER
    return body[:max(0, max_chars - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER


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


# ---- #693: standing rulings — the always-present positive-polarity section ----

# The section renders at the TOP of the briefing, outside _DROP_ORDER: a
# ruling is a human-ratified standing constraint, and budget pressure must
# never silently drop the one section whose whole point is that it cannot
# fade. Worst case (a full cap of maximum-length rulings) costs ~17-20% of
# the default 3000-token budget — pinned by test.
_RULING_HEADER = "Standing rulings (human-ratified — honor these):"


def active_rulings(project_dir=None) -> list[dict]:
    """Every active ruling for the briefing section, newest-activated first
    (ties break on refutation_id — the fold keeps no finer stamp). This is
    the SECTION's order, chosen so the cap slice in ruling_lines keeps the
    newest ratifications; `daimon ruling list` and the viewer lane keep
    refutations.listing's own presentation order.

    Fail-open: ANY error — read, fold, or sort over hand-edited rows —
    yields [] rather than costing the briefing."""
    try:
        rows = refutations.listing(states={"active"}, polarity="ruling",
                                   project_dir=project_dir)
        rows.sort(key=lambda r: (str(r.get("activated_at") or ""),
                                 str(r.get("refutation_id") or "")),
                  reverse=True)
        return rows
    except Exception:
        return []


def ruling_lines(project_dir=None) -> list[str]:
    """The section's rendered lines ([] when no active rulings — the section
    is skeleton furniture, but empty furniture is noise). Verdict, never
    subject: the verdict IS the rule text (cli._print_ruling's contract, one
    vocabulary across surfaces).

    Backstops, both LOUD: more actives than DAIMON_RULING_CAP — a
    hand-edited ledger, or simply LOWERING the cap after activations, a
    supported move the cap guard's own error text invites — renders the
    cap's worth PLUS a note naming how many were withheld (a silent
    truncation of human-ratified constraints is the one failure this
    section must never have); a hand-edited verdict longer
    than the write-time bound is clipped with a visible marker; an empty
    verdict renders nothing. Non-human `text_authored_by` is labeled with
    its own AUTHORITY word (agent / mechanical — CHANNEL_AUTHORITY's
    vocabulary, cli._print_ruling's own label) even after human
    ratification — who wrote the words survives who approved them."""
    rows = [r for r in active_rulings(project_dir)
            if str(r.get("verdict") or "").strip()]
    if not rows:
        return []
    try:
        cap = config.ruling_cap()
    except Exception:
        return []
    shown, over = rows[:cap], rows[cap:]
    lines = [_RULING_HEADER]
    for row in shown:
        verdict = str(row.get("verdict") or "")
        if len(verdict) > refutations._MAX_RULING_TEXT:
            verdict = verdict[:refutations._MAX_RULING_TEXT] + "…"
        authored = row.get("text_authored_by")
        suffix = (f"  [{authored}-written]"
                  if authored and authored != "human" else "")
        lines.append(f"§ {verdict}{suffix}")
    if over:
        plural = "s" if len(over) != 1 else ""
        lines.append(f"  (+{len(over)} active ruling{plural} over cap — "
                     "daimon ruling list shows all)")
    return lines


# ---- #694 PR 2: the recipient-side request panel ---------------------------

# Ruling-shaped (D2): always-present, skeleton furniture, capped and loud on
# overflow — never a section budget pressure is allowed to quietly thin out.
_REQUEST_PANEL_HEADER = "Requests waiting on you (from other projects):"

# An `ask` can be up to `requests._MAX_TEXT` (2000 chars); the panel line is
# meant to be skimmable, not the full record — `daimon request inbox` has the
# rest. Same display-cap idiom as `_truncate_agent_claim` above, and the
# reason the worst-case-cap test can bound the section's cost at all.
_REQUEST_ASK_CHARS = 160


def _truncate_request_ask(ask: str) -> str:
    text = str(ask or "").strip()
    if len(text) <= _REQUEST_ASK_CHARS:
        return text
    return text[:_REQUEST_ASK_CHARS].rstrip() + "…"


def request_panel_lines(project_dir=None) -> list[str]:
    """The recipient-side panel's rendered lines ([] when nothing is
    addressed to this project, or `project_dir` is None — the section is
    skeleton furniture, but empty furniture is noise).

    Fail-open like `ruling_lines`: ANY error from the composer yields []
    rather than costing the briefing. `project_dir` here is the CLI's
    `worldcheck_project` — the caller (render.render_brief /
    cli._render_briefing_body) is the ONE place that decides whether this
    request is same-project (D2's CLI-only gate); this function never
    inspects route or surface on its own."""
    if project_dir is None:
        return []
    try:
        entry = requests.inbox_renderable(project_dir=project_dir)
    except Exception:
        return []
    rows = entry.get("rows") or []
    if not rows:
        return []
    lines = [_REQUEST_PANEL_HEADER]
    for row in rows:
        marker = "  [blocking]" if row.get("blocking") else ""
        lines.append(f"→ {row['request_id']}  "
                     f"{_truncate_request_ask(row.get('ask', ''))}{marker}")
        lines.append(f"  From: {row.get('from_label') or 'an unnamed project'}")
    overflow = entry.get("overflow") or 0
    if overflow:
        plural = "s" if overflow != 1 else ""
        lines.append(f"  (+{overflow} more waiting{plural} — "
                     "daimon request inbox)")
    return lines


# ---- #885: the recipient-side owed panel ------------------------------------

# Registered and frozen the same way the two headings above are (§3 amendment
# 2026-08-27). The wording says OWE deliberately: an item here is not a
# decision waiting on the human, it is work this project already agreed to.
_OWED_PANEL_HEADER = "Requests you accepted and still owe:"


def owed_panel_lines(project_dir=None) -> list[str]:
    """#885: the recipient's OWED panel ([] when this project owes nothing,
    or `project_dir` is None). Same posture as the two panels around it:
    fail-open, skeleton furniture, never silently truncated over RENDER_CAP.

    A third panel rather than more rows in `request_panel_lines`, because the
    verb differs and the reader must not have to infer which from a glyph: an
    undecided ask offers accept and reject, an owed one offers `done`. The
    closing line names that verb for the same reason the inbox panel names
    its own surface."""
    if project_dir is None:
        return []
    try:
        entry = requests.owed_renderable(project_dir=project_dir)
    except Exception:
        return []
    rows = entry.get("rows") or []
    if not rows:
        return []
    lines = [_OWED_PANEL_HEADER]
    for row in rows:
        marker = "  [blocking]" if row.get("blocking") else ""
        lines.append(f"✓ {row['request_id']}  "
                     f"{_truncate_request_ask(row.get('ask', ''))}{marker}")
        lines.append(f"  From: {row.get('from_label') or 'an unnamed project'}")
    overflow = entry.get("overflow") or 0
    if overflow:
        lines.append(f"  (+{overflow} more owed — "
                     "daimon request inbox)")
    lines.append("  Close one with: daimon request done <id> --evidence …")
    return lines


# ---- #694 PR 3: the sender-side verdict panel -------------------------------

_VERDICT_PANEL_HEADER = "Decisions on requests you sent:"

# Mirrors cli/request.py's _MARKS glyph-per-state — a separate small table
# rather than an import, because briefing.py must not depend on the cli
# package (the dependency runs the other way: cli imports briefing/render).
_VERDICT_MARKS = {"needs-info": "?", "accepted": "✓", "rejected": "×",
                  "done": "✔"}


def verdict_panel_lines(project_dir=None) -> list[str]:
    """The sender-side panel's rendered lines ([] when nothing this project
    sent has been decided yet, or `project_dir` is None). Same posture as
    `request_panel_lines`: fail-open, skeleton furniture, never silently
    truncated over RENDER_CAP. `project_dir` is the CLI's
    `worldcheck_project` — same D2 CLI-only gate, same caller contract."""
    if project_dir is None:
        return []
    try:
        entry = requests.verdict_renderable(project_dir=project_dir)
    except Exception:
        return []
    rows = entry.get("rows") or []
    if not rows:
        return []
    lines = [_VERDICT_PANEL_HEADER]
    for row in rows:
        state = str(row.get("state") or "")
        mark = _VERDICT_MARKS.get(state, "?")
        lines.append(f"{mark} {state}  {row['request_id']}  "
                     f"{_truncate_request_ask(row.get('ask', ''))}")
        lines.append(f"  To: {row.get('to') or '?'}")
        note = str(row.get("note") or "").strip()
        if note:
            lines.append(f"  Note: {_truncate_request_ask(note)}")
        done_evidence = str(row.get("done_evidence") or "").strip()
        if done_evidence:
            lines.append(f"  Done: {_truncate_request_ask(done_evidence)}")
    overflow = entry.get("overflow") or 0
    if overflow:
        plural = "s" if overflow != 1 else ""
        lines.append(f"  (+{overflow} more decided{plural} — "
                     "daimon request list)")
    return lines


def render_plain(b: dict, degraded: bool = False, rulings=(),
                 request_lines=(), verdict_lines=(), owed_lines=()) -> str:
    """The deterministic briefing text. Under the #79 budget this is
    BYTE-IDENTICAL to the legacy render(); over it, long items truncate
    (sections preserved) and then whole items drop, lowest value first,
    each cut announced with a trim note. `degraded` (#204) downgrades every
    verbatim label and adds one header note when the receipt is unverifiable."""
    budget = config.brief_max_tokens()
    text = _render_parts(b, {}, degraded, rulings, request_lines,
                         verdict_lines, owed_lines)
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
    text = _render_parts(b, trimmed, degraded, rulings, request_lines,
                         verdict_lines, owed_lines)

    # Stage 2: drop whole items, least valuable first, until the budget holds
    # or only the skeleton remains.
    for key, end in _DROP_ORDER:
        while estimate_tokens(text) > budget and b.get(key):
            items = list(b[key])
            items.pop(-1 if end == "tail" else 0)
            b[key] = items
            trimmed[key] += 1
            text = _render_parts(b, trimmed, degraded, rulings,
                                 request_lines, verdict_lines, owed_lines)
        if estimate_tokens(text) <= budget:
            break
    return text


def _render_parts(b: dict, trimmed: dict, degraded: bool = False,
                  rulings=(), request_lines=(), verdict_lines=(),
                  owed_lines=()) -> str:
    parts = ["While you were away — here's where we left off."]
    if degraded:
        # One header note (#204), embedded in the text so the hook-injected
        # briefing carries it too — not just the human-facing CLI render.
        parts.append("")
        parts.append(DEGRADE_NOTE)
    if rulings:
        # #693: skeleton furniture at the top, outside _DROP_ORDER — the
        # budget loops re-render with the same lines and can only trim the
        # sections below.
        parts.append("")
        parts.extend(rulings)
    if request_lines:
        # #694 PR 2: same posture as rulings above — skeleton furniture, the
        # budget loops re-render with the same lines and can only trim the
        # sections below.
        parts.append("")
        parts.extend(request_lines)
    if verdict_lines:
        # #694 PR 3: same posture — skeleton furniture, outside _DROP_ORDER.
        parts.append("")
        parts.extend(verdict_lines)
    if owed_lines:
        # #885: same posture — skeleton furniture, outside _DROP_ORDER. Last
        # of the four so the reader meets decisions owed TO them before work
        # owed BY them.
        parts.append("")
        parts.extend(owed_lines)

    def _section(header: str, key: str) -> None:
        items = b.get(key) or []
        note = trimmed.get(key, 0)
        if not items and not note:
            return
        parts.append("")
        parts.append(header)
        briefable = key in BRIEFABLE_SECTIONS
        parts.extend(_line(i, degraded, briefable) for i in items)
        if key == "decisions":
            overflow = _overflow_note(b.get("decisions_overflow", 0))
            if overflow:
                parts.append(f"  {overflow}")
        if note:
            parts.append(_trim_note(note))

    if b["external"]:
        parts.append("")
        parts.append("VERIFY BEFORE TRUSTING (state may have changed outside this session):")
        parts.extend(_line(i, degraded, "external" in BRIEFABLE_SECTIONS) for i in b["external"])

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


def render(checkpoint: dict, project_dir=None, worldcheck_project=None) -> str | None:
    """Render the briefing, or None if there is nothing worth surfacing.
    LLM rendering is opt-in (DAIMON_LLM_BRIEFING), post-validated for verbatim
    quote integrity, and falls back to deterministic on any doubt.

    `project_dir` (#693) scopes the standing-rulings section; None (legacy
    callers, hand-built checkpoints in tests) skips the read entirely — an
    unknown project resolves to no ledger path and reads nothing, so the
    guard saves a pointless call rather than preventing a leak.

    `worldcheck_project` (#694 PR 2/3) scopes the incoming-request panel AND
    the sender-side verdict panel — a SEPARATE parameter from `project_dir`,
    never reused, because both panels' gate is narrower than the rulings
    section's: rulings render on every route (including `--slug`), the
    panels only on the CLI same-project path (`cli._render_briefing_body`'s
    own `worldcheck_project`, D2)."""
    b = build(checkpoint)
    rulings = ruling_lines(project_dir) if project_dir is not None else []
    request_lines = (request_panel_lines(worldcheck_project)
                     if worldcheck_project is not None else [])
    verdict_lines = (verdict_panel_lines(worldcheck_project)
                     if worldcheck_project is not None else [])
    owed_lines = (owed_panel_lines(worldcheck_project)
                  if worldcheck_project is not None else [])
    skeleton_blocks = [blk for blk in (rulings, request_lines, verdict_lines,
                                       owed_lines) if blk]
    if b is None:
        # #693/#694: a ruling ratified, a request addressed, or a verdict
        # decided before the first real checkpoint (a day-one action) must
        # still reach context — "nothing worth surfacing" is no longer true
        # when any of the three exist.
        if not skeleton_blocks:
            return None
        return "\n\n".join("\n".join(blk) for blk in skeleton_blocks)
    degraded = receipt_degraded(checkpoint)
    if config.llm_briefing():
        rendered = _render_llm(checkpoint)
        if rendered:
            if _validate_llm_render(rendered, checkpoint):
                # The LLM render carries no per-item marks to degrade; the one
                # header note stays FIRST on every path (#204 — the loudest
                # line never moves below another section). #693/#694: the LLM
                # narrates the CHECKPOINT; rulings and both request panels
                # live outside it, so the deterministic sections are
                # prepended verbatim after the note — never re-narrated,
                # never trusted to a generative pass.
                parts = ([DEGRADE_NOTE] if degraded else [])
                parts.extend("\n".join(blk) for blk in skeleton_blocks)
                parts.append(rendered)
                return "\n\n".join(parts)
            log.warning("llm briefing dropped a verbatim quote — "
                        "falling back to the deterministic render")
    return render_plain(b, degraded, rulings, request_lines, verdict_lines,
                        owed_lines)


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
