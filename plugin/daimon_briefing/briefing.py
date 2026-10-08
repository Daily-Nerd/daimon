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
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

# store/carry import graph checked (#103): neither store, carry, recall,
# scoring, nor serializer imports briefing — no cycle, so this stays a normal
# module-level import (contrast carry.py's own local-import notes, which
# don't apply here). checks_runtime is the #943 stdlib-only runtime module —
# it imports nothing from this package, so it carries no cycle risk either
# (#1093: the manifest-derived enforce lines read it directly).
from . import (capture, checks_host, checks_runtime, config, display,
               llm, pending, receipts, refutations, requests, schema,
               scoring, store)
# Imported as constants, not as the module.
from .amendments import CHANGES as _AMEND_CHANGES
from .amendments import RENDER_STATES as _AMEND_RENDER_STATES
from .amendments import found_label as _amend_found_label
from .marks import (GREETING, ITEM_MARKS, RULING_MARK, VERIFY_PHRASE,
                    WARNING_MARK)

log = logging.getLogger("daimon.briefing")

_VERBATIM_MARK = "✓ verbatim"
_INFERRED_MARK = "~ inferred"
_UNTAGGED_MARK = "? untagged"
# #977: a carried item whose EFFECTIVE last-verified age exceeds the
# staleness budget (#215) renders as unverified INSTEAD of its stored trust
# tag; the tag and age ride in a trailing suffix. Render-time only: the
# stored trust value is never rewritten, and `daimon reverify` restores the
# tag on the next brief via the resolutions fold (a fresh event ts is the
# newest age candidate, same rule stamp_stale_carried applies).
_STALE_CARRIED_MARK = "? unverified"
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


def _trust_label(item) -> str:
    # #977: the stored trust class as a plain word ("verbatim" / "inferred" /
    # "untagged"), the same three-way vocabulary _mark and render._trust_key
    # already agree on, without the mark glyphs. Used by the stale-carried
    # suffix ("was verbatim") so the rendered line names what the tag was
    # before the render-time substitution.
    trust = item.get("trust")
    if trust == "verbatim":
        return "verbatim"
    return "inferred" if trust else "untagged"


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
    return display.shorten(evidence, _AGENT_CLAIM_EVIDENCE_CHARS, hard=True)


# ---- #480 slice 1: resolve handles on open-loop-class items ----

# BRIEFABLE_SECTIONS (build()'s section keys that render a resolve handle) is
# derived from ItemField.briefable beside SECTION_ORDER below.


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


def item_quote(item, text: str, full_quotes: bool = False) -> str:
    """#1129: the quote shown beside an item, one rule for the plain line and
    the rich panel. A bounded span, hidden when the item's text already holds
    it. A budget-shortened copy (select stage 1) carries the verdict taken
    against its ORIGINAL text in `_quote_in_text`, so shortening never brings
    a quote back. `full_quotes` (the LLM path's sizing) charges the stored
    quote whole."""
    if full_quotes:
        return str(item.get("quote") or "").strip()
    if item.get("_quote_in_text"):
        return ""
    return display.quote_span(item.get("quote"), text)


def _line(item, degraded: bool = False, briefable: bool = False,
          full_quotes: bool = False) -> str:
    # #134: dict.get returns the stored None for a present-but-null key (the
    # default only fires for an ABSENT key), so a torn/legacy checkpoint could
    # crash the whole render here. Use the codebase's str(x or "") idiom
    # (store.py, carry.py) — tolerant of null, same as iter_items' stance.
    text = str(item.get("text") or "").strip()
    quote = item_quote(item, text, full_quotes)
    mark = _mark(item, degraded)
    stale_days = item.get("_stale_carried_days")
    was_label = None
    if isinstance(stale_days, (int, float)) and not isinstance(stale_days, bool):
        # #977: past the staleness budget, the stored tag must not read as
        # fresh evidence: render unverified, name the stored tag and the
        # age in a trailing suffix. The stamp is transient (stamp_stale_carried),
        # so this never rewrites the stored trust value.
        was_label = _trust_label(item)
        mark = _STALE_CARRIED_MARK
    base = f'{ITEM_MARKS[0]} [{mark}] {text}'
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
    if was_label is not None:
        base += f" (was {was_label}, carried {stale_days:.0f}d)"
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
        # site (_machine_stamps): closed change vocab, clipped role, truncated
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
                # #1087: an ADDED line — the pinned prefix above (through
                # "unconfirmed: ...") never changes. `found` says WHERE the
                # quote was found, never WHO said it: a plain-text `daimon
                # serialize <file>` turns any file into one role="user"
                # message, so a role alone can never honestly claim a human
                # spoke.
                base += (f'\n  ⚠ agent-proposed amendment — {change} '
                         f'(quote-verified, role: {role}), unconfirmed: '
                         f'"{quote}"'
                         f"\n    found: {_amend_found_label(role)}"
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


def _by_weight(items, item_type, now):
    """Sort a section by #78 effective weight, heaviest first. sorted() is stable,
    so legacy items (no first_seen / no importance -> equal neutral weights) keep
    their serializer order — pre-D-011 checkpoints render exactly as before."""
    return sorted(items, key=lambda i: scoring.effective_weight(i, item_type, now),
                  reverse=True)


def _is_carried(item) -> bool:
    """#1034: a decision inherited from the previous checkpoint. carry.merge
    stamps `carried_from` on every copy it appends; a native item — one this
    session actually produced — never has it."""
    return bool(isinstance(item, dict) and item.get("carried_from"))


def _order_decisions(decisions, now):
    """The native block stays chronological (the serializer's CHRONOLOGY
    contract), then carried decisions by #78 effective weight, heaviest first
    (#1034). carry.merge appends the previous checkpoint's older items at the
    TAIL of the same list, so the raw order is not the render order."""
    native = [i for i in decisions if not _is_carried(i)]
    carried = [i for i in decisions if _is_carried(i)]
    return native + _by_weight(carried, "recent_decision", now)


def build(checkpoint, now=None) -> dict | None:
    """Structured briefing sections, or None if nothing is worth surfacing.
    Deterministic and pure — no LLM, no I/O; `now` is injectable for tests.
    Sections order by #78 effective weight EXCEPT recent_decisions, whose
    NATIVE block stays chronological; carried decisions follow it, by weight.
    Nothing is capped or dropped here (#1128): `select` applies the decision
    cap and the byte budget, with a reason recorded for every item it drops."""
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

    return {
        "external": [i for i in open_qs if i.get("external_state")],
        "open_loops": [i for i in open_qs if not i.get("external_state")],
        "decisions": _order_decisions(decisions, now),
        "active_topic": active if _nonempty(active) else None,
        "beliefs": beliefs,
        "uncertainties": uncertainties,
        "contradictions": contradictions,
        "now": now,
    }


# ---- #103: event-resolved items and machine claims at render time ----

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


def _machine_stamps(checkpoint: dict, snap) -> tuple:
    """The machine-claim annotations, pure over a `view.Snapshot`: returns
    `(checkpoint, candidates)`; the input UNCHANGED (same object) and `[]`
    when nothing is stamped, so the common case costs nothing. Nothing is
    ever dropped here: what the reader may not see is `view`'s decision.

    #14: a "supersede-candidate:<new-id>" latest event is a machine
    SUGGESTION, not a resolution (`store.is_resolved` says so: the loop stays
    live). The item gets a transient `_supersede_candidate = "<new-id>"`;
    id-bearing only, by construction.

    #480 slice 4: a still-pending agent resolve candidate (latest event
    `resolving-candidate`, source="agent") gets `_agent_claim = "<evidence
    quote>"`, via `capture._pending_agent_candidates` so the fold that decides
    idempotence stays in one place. Kept OUT of `candidates` on purpose: that
    list is #14's own "likely superseded (unconfirmed)" subsection, a
    different suggestion with a different confirm/reject pair.

    #691: `snap.amendments` is `amendments.render_groups`' shape (verified or
    ratified ONLY). The item gets a transient `_amend` list of bounded
    payloads, re-checked HERE (change vocabulary, render state, role clipped,
    quote truncated at render): a row edited on disk must not ride into the
    injected context. A resolved item takes no annotation: it dies with the
    loop. An item can carry a candidate AND an amendment."""
    resolutions = snap.resolutions
    resolved_refs = snap.resolved_refs
    candidate_refs: dict[str, str] = {}
    for ref, evt in resolutions.items():
        if not isinstance(evt, dict):
            continue
        status = str(evt.get("status") or "")
        if status.lower().startswith("supersede-candidate") and ":" in status:
            new_id = status.split(":", 1)[1].strip()
            # Shape gate: the status field is free-form by design, so the
            # payload after the colon can be ANY text, and it rides verbatim
            # into the rendered confirm-command suggestion and the hook-
            # injected LLM context (an injection surface). Only an id-shaped
            # payload earns a stamp; a malformed machine claim earns no
            # surface at all. Mirrors carry._ID_SHAPE, with the hex run
            # bounded (fullmatch on attacker-adjacent input wants bounded
            # quantifiers).
            if new_id and _CANDIDATE_ID_SHAPE.fullmatch(new_id):
                candidate_refs[ref] = new_id
    agent_claim_refs = capture._pending_agent_candidates(resolutions)
    amend_refs = snap.amendments
    if not candidate_refs and not agent_claim_refs and not amend_refs:
        return checkpoint, []

    to_stamp = []  # [(section, key, index, event, new_id)]
    to_stamp_claim = []  # [(section, key, index, evidence)]
    to_stamp_amend = []  # [(section, key, index, payloads)]
    for section, key in schema.ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if not item_id:
                continue
            if item_id in candidate_refs:
                to_stamp.append((section, key, idx, resolutions[item_id],
                                 candidate_refs[item_id]))
            elif item_id in agent_claim_refs:
                to_stamp_claim.append(
                    (section, key, idx, agent_claim_refs[item_id]))
            entry = amend_refs.get(item_id)
            if entry is None or item_id in resolved_refs:
                continue
            rows = (entry.get("rows") if isinstance(entry, dict) else entry)
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
                and str(rec.get("state") or "") in _AMEND_RENDER_STATES]
            overflow = (entry.get("overflow", 0)
                        if isinstance(entry, dict) else 0)
            if payloads:
                to_stamp_amend.append(
                    (section, key, idx,
                     {"rows": payloads,
                      "overflow": overflow if isinstance(overflow, int)
                      else 0}))
    if not to_stamp and not to_stamp_claim and not to_stamp_amend:
        return checkpoint, []

    out = copy.deepcopy(checkpoint)
    candidates = []
    for section, key, idx, evt, new_id in to_stamp:
        item = out[section][key][idx]
        item["_supersede_candidate"] = new_id
        candidates.append((key, item, evt))
    for section, key, idx, evidence in to_stamp_claim:
        out[section][key][idx]["_agent_claim"] = evidence
    for section, key, idx, payload in to_stamp_amend:
        out[section][key][idx]["_amend"] = payload
    return out, candidates


def stamp(checkpoint, snap, now, *, with_stale: bool = True) -> tuple:
    """#1132 PR 7a: every transient annotation the renderers read, pure over a
    `view.Snapshot`: the machine claims (`_supersede_candidate`,
    `_agent_claim`, `_amend`), the #268 witness count (`_corroborated`) and
    the #977 stale mark (`_stale_carried_days`). Returns `(checkpoint,
    candidates, stale_items)`; `checkpoint` is a copy when anything is
    stamped, the input otherwise. It reads no ledger and drops nothing: the
    checkpoint a caller hands in has already been through `view`."""
    if not isinstance(checkpoint, dict):
        return checkpoint, [], []
    out, candidates = _machine_stamps(checkpoint, snap)
    out = mark_corroborated(out, snap.corroborations)
    stale: list = []
    if with_stale:
        out, stale = stamp_stale_carried(out, snap.resolutions, now)
    return out, candidates, stale


# ---- #268: corroboration — independent sightings, stamped for the render ----


def mark_corroborated(checkpoint, corroborations: dict):
    """Stamp corroborated items with a transient `_corroborated = N` count and
    return the result; pure, no I/O — the caller does the read
    (`view.snapshot` folds it once, #268 slice 4).

    `corroborations` is `store.corroborations()`'s shape, keyed by bare item
    id. N = 1 + the EFFECTIVE origins: the origin of record is the claim's
    first sighting, and every session in `origins` is one more. `recorded` is
    deliberately not counted — a witness discounted by a later contradiction
    stays on the record without paying, and re-deriving that verdict here
    would be a second opinion about a question the fold already answered.
    Below `CORROBORATION_MIN` nothing is stamped at all, so an uncorroborated
    item is byte-identical to its pre-#268 render.

    Transient like the machine-claim stamps and worldcheck's flags: the
    count lives in events.jsonl, and a `_corroborated` key on a stored
    checkpoint would be a second, forgeable copy of it. Nothing here writes.

    No corroborations, or a non-dict checkpoint -> the input UNCHANGED, same
    no-op idiom as `stamp`/carry.merge: no copy unless something is actually
    stamped, so the common case (nothing witnessed yet) costs nothing."""
    if not isinstance(checkpoint, dict) or not corroborations:
        return checkpoint

    # Dry run over the ORIGINAL, then one deepcopy — `_machine_stamps`' shape.
    to_stamp = []  # [(section, key, index, n)]
    for section, key in schema.ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            entry = corroborations.get(item.get("id"))
            if not isinstance(entry, dict):
                continue
            # #983 change 3: an item whose first writer was a provisional
            # never accrues the badge, even against a ledger row that
            # predates this fix — store.corroboration_origins_for reads the
            # item's own origin_session, already in hand here, no extra I/O.
            n = 1 + len(store.corroboration_origins_for(item, entry))
            if n >= CORROBORATION_MIN:
                to_stamp.append((section, key, idx, n))

    if not to_stamp:
        return checkpoint

    out = copy.deepcopy(checkpoint)
    for section, key, idx, n in to_stamp:
        out[section][key][idx]["_corroborated"] = n
    return out


# ---- #215: staleness budget — carried items nobody has world-checked ----


def _carried_age_days(item, resolutions, now):
    """#977: the EFFECTIVE last-verified age in days for a CARRIED item, or
    None when the item is not carried or has no parseable stamp at all
    (fail-open: an unparseable stamp is never itself a false alarm). Single
    source for the newest-of-candidates rule so the render stamp
    (stamp_stale_carried) and the loops listing age (`listing_age_days`) can
    never disagree about an age.

    `resolutions` is a `{item_ref: latest_event}` mapping, the fold
    `view.Snapshot.resolutions` holds."""
    if not isinstance(item, dict) or not item.get("carried_from"):
        return None
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
        return None  # no parseable stamp at all: fail open, not stale
    # #1128: a stamp in the future (clock skew, a teammate's machine) clamps
    # to age 0, never a negative age that a threshold compare or a sort key
    # would have to special-case.
    return max(0.0, (now - max(candidates)) / 86400.0)


def listing_age_days(item, resolutions, now):
    """#1128: the age `daimon loops` prints per row, from the same rule the
    stale classification uses. A carried item gets its effective last-verified
    age (`_carried_age_days`); a native one has no carry history, so its age
    is time since `first_seen`. None when nothing parses (fail-open, the row
    prints no age)."""
    if item.get("carried_from"):
        return _carried_age_days(item, resolutions, now)
    born = store._created_epoch(item.get("first_seen"))
    return None if born is None else max(0.0, (now - born) / 86400.0)


def stamp_stale_carried(checkpoint, resolutions: Mapping, now,
                        threshold_days=None):
    """#977: the render-time half of the staleness budget. Returns
    (checkpoint, stale_items) where every carried item past the threshold
    carries a transient `_stale_carried_days` stamp (its effective age in
    days) that `_line` / render._rich_brief turn into the `[? unverified]`
    mark plus the `(was <tag>, carried Nd)` suffix.

    A carried item's age is the NEWEST of `last_verified`, the latest
    resolutions event `ts` for its id (`resolutions` may be any Mapping: a
    `view.Snapshot` holds a read-only one) and `first_seen`; an unparseable
    stamp contributes nothing, and an item with none is never stale. Transient
    like mark_corroborated's count: the stamp rides the IN-MEMORY item only,
    deep-copied when something is stamped and returned UNCHANGED otherwise, so
    the stored trust value is never rewritten and a no-stale brief costs
    nothing."""
    if threshold_days is None:
        threshold_days = config.stale_days()
    if not isinstance(checkpoint, dict):
        return checkpoint, []
    resolutions = resolutions if isinstance(resolutions, Mapping) else {}
    to_stamp = []  # [(section, key, index, age_days)]
    for section, key in schema.ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            age_days = _carried_age_days(item, resolutions, now)
            if age_days is not None and age_days > threshold_days:
                to_stamp.append((section, key, idx, age_days))
    if not to_stamp:
        return checkpoint, []
    out = copy.deepcopy(checkpoint)
    stale = []
    for section, key, idx, age_days in to_stamp:
        item = out[section][key][idx]
        item["_stale_carried_days"] = age_days
        stale.append(item)
    return out, stale


# ---- #1128: the one annotation step every host shares ----


class Annotated(NamedTuple):
    """What `prepare` hands a host: the checkpoint after the view and the
    stamps (None when there is none), and the facts the host reports.
    `withheld` is `Opened.withheld` (what `view` removed, never its text),
    `events` the resolution fold, `suppressed` the loops a resolution closed
    and `quarantined` the items a human quarantine removed (a forgotten item
    is counted nowhere: it must read as absent). `snapshot` is the reader's
    own, whichever body was served, and `notes` its health lines, each
    starting with a warning sign."""
    checkpoint: Any
    withheld: Any
    events: Any
    stale_items: list
    # worldcheck.check's counters (LEDGER_KEY popped) or None when it did not
    # run; `ledger_rows` are the rejection-ledger rows the CALLER writes
    # (worldcheck itself writes nothing, by contract).
    worldcheck: dict | None
    ledger_rows: list
    opened: Any = None
    snapshot: Any = None
    suppressed: int = 0
    quarantined: int = 0
    notes: tuple = ()
    fell_back: bool = False


def prepare(project, now, *, live: bool = True, worldcheck_project=None,
            route=None, stamps: bool = True, opened=None) -> Annotated:
    """#1132 PR 7a: the one preparation every briefing host shares (the CLI
    brief, the MCP tool, the Hermes hook, `daimon loops` and the teammates):
    `view.open` (what the reader may see), then `stamp` (the transient marks
    the renderers read), then the optional worldcheck.

    `route` is the store route (default `Route.OWN`; the CLI brief asks for
    OWN_ELSE_GLOBAL and reads `fell_back` off the result). `live=False`
    keeps loops a resolution closed. `stamps=False` skips the stamps (the
    teammate blocks never carried them). `opened` hands in an `Opened` the
    caller already has (`view.team`) instead of opening `project`.

    No try blocks: a ledger that cannot be read is a health value in the
    snapshot (an unreadable trust ledger CLOSES the view), and a raise from
    `view` is a bug the host reports. Worldcheck is the one annotator doing
    I/O, runs only when `worldcheck_project` is set and the flag is on, never
    on a global-fallback body (its `gh` probes would answer for the wrong
    repo), only RETURNS its stats and ledger rows, and stays fail-open."""
    from . import view
    if opened is None:
        opened = view.open(project, live=live,
                           route=route if route is not None
                           else store.Route.OWN)
    snap = opened.snapshot
    checkpoint = opened.checkpoint
    stale_items: list = []
    if stamps and isinstance(checkpoint, dict):
        checkpoint, _candidates, stale_items = stamp(checkpoint, snap, now)
    wc_stats = None
    ledger_rows: list = []
    if (isinstance(checkpoint, dict) and worldcheck_project
            and not opened.fell_back and config.worldcheck_enabled()):
        try:
            from . import worldcheck
            wc_stats = dict(worldcheck.check(checkpoint, worldcheck_project))
            ledger_rows = list(wc_stats.pop(worldcheck.LEDGER_KEY, ()))
        except Exception:
            wc_stats = None
            ledger_rows = []
    return Annotated(
        checkpoint, opened.withheld, snap.resolutions, stale_items, wc_stats,
        ledger_rows, opened, snap, opened.suppressed,
        sum(1 for w in opened.withheld if w.reason == "quarantine"),
        snap.notes(), opened.fell_back)


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


def _byte_len(text: str) -> int:
    """UTF-8 byte length (#1044): what the hosts that spill actually measure.
    A char count under-counts: the render carries multi-byte glyphs (section
    signs, arrows, ellipses) that are 2-3 bytes each in UTF-8."""
    return len(text.encode("utf-8"))


def _log_render_size(text: str, budget) -> None:
    """#1044: the rendered byte size and its token estimate, logged at the
    place the budget is applied, so the next drift between "under budget" and
    "still spills" is visible in the log instead of discovered from a host's
    truncated preview. `budget` is the EFFECTIVE byte budget this call used
    (`effective_budget`; a caller such as `render.render_brief` may have
    passed a reduced `max_bytes`), or None when unbounded."""
    log.debug("daimon: briefing rendered %d bytes (~%d tokens estimated, "
              "byte budget %s)", _byte_len(text), estimate_tokens(text),
              budget)


def truncate_preserving_sections(text: str, max_len: int, *, measure=len) -> str:
    """Cut `text` to max_len (per `measure`), keeping **Label:** sections over
    filler: if the labeled sections alone fit, they ARE the truncation; when
    they do not fit the cut still lands INSIDE them, and only a section-less
    text falls back to a blind head-cut of the raw text. Always appends a
    visible marker — silent truncation reads as 'this is everything' when it
    isn't.

    `measure` defaults to `len` (characters, #79's own unit). #1044's final
    byte ceiling passes `measure=_byte_len` instead: same algorithm, same
    marker, counted in UTF-8 bytes so a cut lands where the host's own byte
    count says it should, not a char count away from it. The cut point is
    found by search over `measure(body[:k])` rather than direct index math
    (safe for either unit; the char case still recovers today's exact split
    because `measure=len` is additive one-char-at-a-time).

    #489: the over-budget case used to fall through to the raw head-cut, which
    returned unlabeled preamble and dropped every section it had just found —
    the inverse of the contract, and worst on the longest, most structured
    items. Cutting the joined sections degrades predictably instead: the
    leading label survives, and what is lost is the tail rather than all of it.
    """
    if measure(text) <= max_len:
        return text
    parts = _SECTION_RE.findall(text)
    body = "\n".join(parts) if parts else text
    marker_len = measure(_TRUNCATION_MARKER)
    if parts and measure(body) + marker_len <= max_len:
        return body + _TRUNCATION_MARKER
    limit = max(0, max_len - marker_len)
    lo, hi = 0, len(body)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(body[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return body[:lo] + _TRUNCATION_MARKER


# ---- #693: standing rulings — the always-present positive-polarity section ----

# The section renders at the TOP of the briefing, outside the drop order: a
# ruling is a human-ratified standing constraint, and budget pressure must
# never silently drop the one section whose whole point is that it cannot
# fade. Worst case (a full cap of maximum-length rulings) costs ~17-20% of
# the default 3000-token budget — pinned by test.
_RULING_HEADER = "Standing rulings (human-ratified — honor these):"


class RulingsRead(NamedTuple):
    """The result of one attempt to read standing rulings (#962). `state` is
    exactly one of "unresolved", "no-bucket", "unreadable", "read" — see
    `rulings_read`. `rows` is `[]` for every state but "read". `path` is the
    resolved refutations.jsonl path, or None exactly when `state` is
    "unresolved" — no other combination occurs.

    `rulings_read` never raises; it always returns one of the four states.
    `active_rulings` never raises either, and returns a plain list — a host
    that only wants "do I have anything to enforce" wraps that one, a host
    that must react to WHY an answer came back empty reads this one."""
    rows: list[dict]
    state: str
    path: Path | None


def rulings_read(project_dir=None, *, read=None) -> RulingsRead:
    """The pinned in-process read for standing rulings, sub-0.1ms against
    100ms+ for a `daimon ruling list --json` subprocess (#962). Every row
    matches `active_rulings`'s order: newest-activated first, ties broken on
    refutation_id (the fold keeps no finer stamp).

    `active_rulings` is reimplemented on top of this — the two cannot drift.
    Where it collapsed four distinct facts into one empty list, this names
    them:

    - "unresolved": `refutations._path` returned None, or raised, before a
      ledger path was ever produced — the project could not be identified at
      all (an unrouted call, or `config` itself failing to resolve, e.g. a
      corrupt `~/.daimon/env` file). `path` is None. Checked FIRST: nothing
      downstream is attempted once this fires.
    - "no-bucket": the path resolved, but the project's bucket directory
      does not exist — a mis-resolved `--project`, never written from.
    - "unreadable": the path resolved and the bucket exists, but the read
      could not be vouched for (`jsonl.Read.cannot_scan`: an OS error such as
      permissions, a symlink loop or a directory in the ledger's place, a
      transient failure that outlasted the retries, an undecodable byte), or
      the fold/sort raised over hand-edited
      rows (a stray non-list `anchors`/`evidence` on a hand-built row is one
      way to land here — known, not fixed by this function). `rows` is [].
    - "read": a successful read, including a bucket that simply carries no
      ledger yet (a clean empty read per `bucket_exists`'s own docstring)
      and a ledger whose malformed lines stay skipped and invisible, same
      as always.

    `read` is the `jsonl.read` result of this ledger when the caller (the
    view's snapshot) already holds it, so the file is read once.
    """
    try:
        path = refutations._path(project_dir)
    except Exception:
        return RulingsRead(rows=[], state="unresolved", path=None)
    if path is None:
        return RulingsRead(rows=[], state="unresolved", path=None)
    try:
        if not refutations.bucket_exists(project_dir):
            return RulingsRead(rows=[], state="no-bucket", path=path)
        got = refutations.read_events(project_dir, read=read)
        if got.unscannable:
            return RulingsRead(rows=[], state="unreadable", path=path)
        records = refutations.fold(got.rows)
        rows = [r for r in records.values()
                if r.get("state") == "active" and r.get("polarity") == "ruling"]
        rows.sort(key=lambda r: (str(r.get("activated_at") or ""),
                                 str(r.get("refutation_id") or "")),
                  reverse=True)
    except Exception:
        return RulingsRead(rows=[], state="unreadable", path=path)
    return RulingsRead(rows=rows, state="read", path=path)


class LayerRead(NamedTuple):
    """One layer's ruling read (#1093), in the same four-state vocabulary
    `rulings_read` documents for a project's own bucket ("unresolved",
    "no-bucket", "unreadable", "read"). `layer` is the absolute owning
    directory `config.layer_scopes` named — never a slug, since
    `layer_scopes` only ever names real ancestor directories."""
    layer: str
    rows: list[dict]
    state: str


class LayerRulingsRead(NamedTuple):
    """Per-layer reads for every eligible ancestor of `project_dir` (#1093),
    GLOBAL FIRST (farthest from the project), then progressively nearer —
    the render order `active_rulings` and `ruling_lines` both use.
    `config.layer_scopes` itself returns nearest-first (the order enforcement
    reasons about); this reverses it, once, here.

    Empty for a project with no eligible layers: `config.layer_scopes`
    returned [] (a slug or non-existent path, a tenant-scoped home, a
    project outside home, or simply no ancestor layer at all). Never raises:
    `layer_scopes` already never raises, and the guard here means a future
    change to it cannot reintroduce a crash on this read path either."""
    layers: list[LayerRead]


def layer_rulings_read(project_dir=None) -> LayerRulingsRead:
    try:
        scopes = config.layer_scopes(project_dir)
    except Exception:
        return LayerRulingsRead(layers=[])
    layers = []
    for layer in reversed(scopes):  # nearest-first -> global-first
        read = rulings_read(layer)
        layers.append(LayerRead(layer=layer, rows=read.rows, state=read.state))
    return LayerRulingsRead(layers=layers)


def _own_read(snap):
    """The project's own `RulingsRead` out of a snapshot, so a briefing reads
    the refutations ledger once. None (no snapshot) means "read it"; a
    snapshot whose fold raised (rulings None) reads as unreadable."""
    if snap is None:
        return None
    if snap.rulings is None:
        return RulingsRead(rows=[], state="unreadable", path=None)
    return snap.rulings


def active_rulings(project_dir=None, own=None) -> list[dict]:
    """Every active ruling for the briefing section: every eligible layer's
    active rulings (#1093), global first then progressively nearer, followed
    by the project's OWN active rulings — within each group, newest-
    activated first (ties break on refutation_id — the fold keeps no finer
    stamp). This is the SECTION's order, chosen so the cap slice in
    ruling_lines keeps the newest ratifications within each group;
    `daimon ruling list` and the viewer lane keep refutations.listing's own
    presentation order.

    Every row carries `inherited_from` (#1093): the absolute owning layer
    directory for a layer row, `None` for the project's own row. This is an
    IN-MEMORY tag only — nothing is ever written back into any ledger. A
    layer row carrying a `request_policy` is dropped entirely: the request
    fold and write boundary read only the project's OWN bucket
    (`requests.active_request_policies`), so rendering an inherited policy
    would tell the agent it may do something the write boundary refuses. The
    merged view is deduped by refutation_id, OWN ROW WINS (a ruling promoted
    from a layer into the project itself renders once, with no inherited
    tag) — the project's own read runs LAST here specifically so it always
    overwrites a same-id layer entry already in the merge.

    Fail-open: ANY error — path resolution, read, fold, or sort over
    hand-edited rows, a missing bucket, an unreadable ledger, a layer-walk
    failure — yields [] rather than costing the briefing. `rulings_read` and
    `layer_rulings_read` already catch all of these themselves, but the
    guard here is repeated on purpose: this is the pinned fail-open API
    (#940), and a future change to either sibling must not be able to
    reintroduce a crash here by accident. A host that needs to tell the
    failures apart reads `rulings_read` (own bucket) or `layer_rulings_read`
    (per layer) instead (#962, #1093)."""
    try:
        return _merged_active_rulings(project_dir, own=own)
    except Exception:
        return []


def _merged_active_rulings(project_dir=None, own=None) -> list[dict]:
    combined: dict = {}
    order: list = []

    def _add(row, inherited_from):
        rid = row.get("refutation_id")
        if not rid:
            return
        tagged = dict(row)
        tagged["inherited_from"] = inherited_from
        if rid not in combined:
            order.append(rid)
        combined[rid] = tagged  # last write wins; own is added last below

    for layer in layer_rulings_read(project_dir).layers:
        for row in layer.rows:
            if isinstance(row.get("request_policy"), dict):
                continue  # #1093: an inherited policy grants nothing here
            _add(row, layer.layer)
    for row in (own if own is not None else rulings_read(project_dir)).rows:
        _add(row, None)
    return [combined[rid] for rid in order]


# ---- #1089: code-enforced rulings render as one compact line -------------
#
# A ruling whose authority lives in daimon's own code — a `request_policy`
# the request fold and write boundary enforce (#961 slices 4-5), or an
# `enforce` check the pre-action hook denies against (#943) — costs the same
# render bytes as a prose ruling the agent must actually read, for no
# reason: the agent is bound by it either way. Only these two classes get a
# compact line, built from their own fields, never the human verdict; every
# other ruling (and either of these two when its fields cannot be read
# cleanly) renders its prose exactly as before — the header, the `§ `
# prefix, the over-cap note and the `[<authority>-written]` suffix are
# frozen for that path.

_POLICY_LEGEND = ("  info: answerable from existing artifacts, asks for no "
                  "change or effort; anything else stays work")


def _slug_label(slug) -> str:
    """A short, human-shaped display name for a `request_policy` slug —
    never the raw slug itself (#1089). Reads the same bucket listing
    `daimon projects` does: a stamped `project_name` (#672) when this
    slug's bucket has one. Absent that (a torn bucket, a pre-#672
    checkpoint, or a policy naming a project with no bucket here yet), a
    `store.project_slug`-shaped slug is always a flattened absolute path
    (leading '-', since every real project directory starts with '/'), so
    its last '-'-joined token is at least bounded and human-shaped even
    when it doesn't recover the real directory name; anything else (a
    short mnemonic slug — `_POLICY_SLUG_RE` allows one, and a hand-ratified
    grant may name one directly) is already a label and passes through."""
    slug = str(slug or "")
    for row in store.list_buckets():
        if row.get("slug") != slug:
            continue
        name = (row.get("checkpoint") or {}).get("project_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        break
    if not slug.startswith("-"):
        return slug
    return slug.rsplit("-", 1)[-1] or slug


def _policy_line(policy) -> str | None:
    """One compact line for a `request_policy`-carrying ruling, or None when
    its fields don't parse as one of the two ratified shapes (#961) — the
    caller falls back to prose rather than rendering nothing."""
    verb = policy.get("verb")
    kind = policy.get("kind")
    by = policy.get("by")
    if by != "agent" or not kind:
        return None
    if verb == "open":
        to = policy.get("to")
        if not to:
            return None
        return f"{RULING_MARK} policy: agent may open {kind} asks → {_slug_label(to)}"
    if verb == "accept":
        sender = policy.get("sender")
        if not sender:
            return None
        return (f"{RULING_MARK} policy: {_slug_label(sender)}'s agent may accept "
                f"{kind} asks here")
    return None


def _check_line(row, check) -> str | None:
    """One compact line for an `enforce` check-carrying ruling, but only
    when THIS briefed host actually delivers `enforce` for it — otherwise
    None, so the caller falls back to the full prose (#1089).

    The host is read from `config.capture_host()`, the same trusted hint
    capture already forwards (#594) — a capture-invoking hook now tags the
    `daimon brief` subprocess with its own host name the identical way it
    already tags its own session-end capture. `checks_host.PROFILES.get`
    on an unknown or absent host, and `mode_for` on a missing profile, both
    resolve to `unsupported` by construction — Windsurf (`unsupported`),
    Kimi (no profile at all) and an undeterminable host all fall through
    to prose here with no special-casing."""
    if check.get("intent") != "enforce":
        return None
    profile = checks_host.PROFILES.get(config.capture_host() or "")
    if checks_host.mode_for(profile, "enforce") != "enforce":
        return None
    subject = str(row.get("subject") or "").strip()
    ruling_id = str(row.get("refutation_id") or "").strip()
    if not subject or not ruling_id:
        return None
    return f"{RULING_MARK} enforced: {subject} (daimon ruling show {ruling_id})"


def _compact_line(row):
    """`(line, class)` for a code-enforced ruling that rendered compact, or
    `(None, None)` to fall back to its prose. Policy first, then an
    enforced check (a ruling is not expected to carry both, but nothing
    here assumes it can't); either lookup failing outright — a malformed
    hand-edited field, a filesystem read inside `_slug_label`, a broken
    host lookup — is caught here so ONE ruling's bad data degrades to its
    own prose line rather than dropping the whole section (#940's fail-open
    posture, held per-row instead of per-section for this one path)."""
    policy = row.get("request_policy")
    if isinstance(policy, dict):
        try:
            line = _policy_line(policy)
        except Exception:
            line = None
        if line is not None:
            return line, "policy"
    check = row.get("check")
    if isinstance(check, dict):
        try:
            line = _check_line(row, check)
        except Exception:
            line = None
        if line is not None:
            return line, "check"
    return None, None


def _layer_suffix(row) -> str:
    """The `[from ~/work]` render tag for an inherited row (#1093), in the
    style of the existing `[<authority>-written]` suffix — "" for a project's
    own row (`inherited_from` is `None`). When both suffixes apply they
    render in a PINNED order, authority first: `§ verdict  [agent-written]
    [from ~/work]` — a future change to this order must update both this
    function and the echo filter's key-building in store.py, which mirrors
    it exactly."""
    inherited_from = row.get("inherited_from")
    if not inherited_from:
        return ""
    return f"  [from {config.home_relative(inherited_from)}]"


def _is_slug_input(project_dir) -> bool:
    """True when `project_dir` names a bucket SLUG rather than a directory
    path (#1093) — `brief --slug` and MCP `daimon_brief(slug=...)` pass a
    bare slug straight through as `project_dir`.

    Mirrors `config.resolve_project_dir`'s own `looks_like_path` test (a
    path SEPARATOR, not disk existence): a real `store.project_slug`-shaped
    slug never contains one (it is a flattened absolute path, `os.sep`
    replaced throughout), while every directory path does, whether or not it
    exists on THIS filesystem. Existence cannot be the test — the suite (and
    production callers routing by an as-yet-uncreated project path) pass
    plenty of well-formed absolute paths that name no real directory, and
    `layer_scopes` already answers [] for those on its own terms without
    this function calling them a slug. Never raises (an `os` failure reads
    as "not a slug", the same silence every other layer-read failure gets
    here)."""
    if not project_dir:
        return False
    try:
        text = str(project_dir)
        looks_like_path = (os.sep in text
                           or (os.altsep is not None and os.altsep in text))
        return not looks_like_path
    except Exception:
        return False


def _inherited_notes(project_dir) -> list[str]:
    """The loud, but never section-costing, notes about the layer walk
    (#1093): a slug can never resolve one (`layer_scopes` always answers []
    for a slug, and a silent [] would read as "no layers exist" rather than
    "layers were never even asked"); an unreadable layer ledger costs that
    layer's rows but is never silent either. Both render ONLY when the
    caller already decided the section renders at all — an empty section
    stays empty furniture-free, per #940."""
    if _is_slug_input(project_dir):
        return ["  (inherited rulings not resolved for a slug)"]
    try:
        layers = layer_rulings_read(project_dir).layers
    except Exception:
        return []
    notes = []
    for layer in layers:
        if layer.state not in ("read", "no-bucket"):
            notes.append(f"  (inherited rulings from "
                        f"{config.home_relative(layer.layer)} unreadable)")
    return notes


def _manifest_enforce_lines(project_dir, rendered_ids: set) -> list[str]:
    """One compact line per `checks_runtime.armed_for(project_dir)` manifest
    entry whose ruling id is not already in `rendered_ids` (#1093).

    Returns EVERY matching entry, uncapped — the caller (`ruling_lines`)
    gives these lines only the room left under `DAIMON_RULING_CAP` after the
    ledger rows and folds the rest into the same over-cap count: a manifest
    line is still one ruling in force against the cap, not a bonus outside
    it.

    This is the worktree case: `config.layer_scopes` never treats anything
    inside a git working tree as a layer, so a worktree's ledger walk never
    sees the parent repo's rulings at all — but the pre-action hook still
    enforces them there, because `armed_for` matches by directory PREFIX,
    with no git awareness. Reading the manifest directly is how this section
    stays honest about what actually fires in a worktree, without re-walking
    layers (`refutations.listing` — and therefore `checks._wanted` — must
    keep its own un-widened default, or a child sync would arm the same
    check a second time under its own root).

    Gated on the SAME host check `_check_line` uses (`config.capture_host()`
    delivering `enforce` per `checks_host`): a manifest entry carries no
    verdict prose to fall back to, only structural fields (`match`,
    `project_dir`, `ruling_id`) that were always meant for a hook to read,
    so a host that does not deliver `enforce` sees no line here at all,
    never a fallback. Filtered to `intent == "enforce"` — a `warn` or
    `record-only` check blocks nothing, so a worktree missing one is lower
    stakes than the enforce case this exists for. Fail-open throughout: `[]`
    on any failure, and one bad entry is skipped rather than dropping the
    rest."""
    try:
        if not isinstance(project_dir, str) or not project_dir:
            return []
        if not (os.path.isabs(project_dir) and os.path.isdir(project_dir)):
            return []
        profile = checks_host.PROFILES.get(config.capture_host() or "")
        if checks_host.mode_for(profile, "enforce") != "enforce":
            return []
        manifest = checks_runtime.load_manifest()
        entries = checks_runtime.armed_for(project_dir, manifest)
    except Exception:
        return []
    lines = []
    for entry in entries:
        try:
            if not isinstance(entry, dict) or entry.get("intent") != "enforce":
                continue
            ruling_id = str(entry.get("ruling_id") or "")
            if not ruling_id or ruling_id in rendered_ids:
                continue
            match = str(entry.get("match") or "")
            root = str(entry.get("project_dir") or "")
            if not match or not root:
                continue
            lines.append(f"{RULING_MARK} enforced from {config.home_relative(root)}: "
                        f"{match}  [{ruling_id}]")
            rendered_ids.add(ruling_id)
        except Exception:
            continue
    return lines


def prose_mask(snap):
    """`text -> text` for the prose a panel prints: a whole value that is
    forgotten or quarantined in `snap` becomes the one-line withheld marker
    (reason and record id, never the value). None (a caller with no snapshot)
    masks nothing. A closed snapshot is NOT applied to prose here: the
    standing rulings and the panels are human-ratified furniture that must
    keep rendering when the trust ledger is unreadable (the items are what
    the closed view withholds)."""
    if snap is None:
        return lambda text: text
    from . import view

    def mask(text):
        verdict = view.prose_verdict(text, snap, closed_masks=False)
        return display.withheld_marker(verdict) if verdict else text
    return mask


def ruling_lines(project_dir=None, *, snap=None) -> list[str]:
    """The section's rendered lines ([] when there is nothing at all to show
    — the section is skeleton furniture, but empty furniture is noise).
    Verdict, never subject, for a PROSE ruling: the verdict IS the rule text
    (cli._print_ruling's contract, one vocabulary across surfaces). A
    code-enforced ruling (#1089: a `request_policy` or an `enforce` check
    this host delivers) renders a compact line from its own fields instead
    — see `_compact_line` — plus one fixed legend line, once, when at least
    one policy ruling rendered compact.

    #1093: layer rulings (`active_rulings` — global first, then
    progressively nearer, then the project's own) render before the
    project's own, each layer row suffixed `[from ~/work]` (`_layer_suffix`)
    — an inherited `request_policy` row never reaches here at all
    (`active_rulings` drops it). After the ledger rows, one compact line per
    manifest entry `checks_runtime.armed_for` finds for this directory whose
    id was not already rendered (`_manifest_enforce_lines`) — this is what
    keeps a WORKTREE (never a layer) honest about a parent repo's enforce
    checks even though its ledger walk cannot see them. A manifest line is
    still one ruling against DAIMON_RULING_CAP: it gets only the room left
    after the ledger rows, and whatever does not fit folds into the same
    over-cap count as a withheld ledger row, never rendered as a free bonus
    outside the cap. The section renders when EITHER the ledger rows or the
    manifest lines are non-empty — a worktree with an empty ledger but an
    armed parent check is not "nothing to show" — and only then do the
    loud-but-non-costing notes (`_inherited_notes`: an unreadable layer, or
    a slug that resolves no layers at all) get a line.

    Backstops, both LOUD: more actives than DAIMON_RULING_CAP — a
    hand-edited ledger, or simply LOWERING the cap after activations, a
    supported move the cap guard's own error text invites — renders the
    cap's worth PLUS a note naming how many were withheld, ledger rows and
    manifest lines combined (a silent truncation of human-ratified
    constraints, or of what actually fires in a worktree, is the one
    failure this section must never have; the note points at `daimon ruling
    list --inherited`, the flag #1095 ships, since the withheld count can
    include inherited rows too); a hand-edited verdict longer than the
    write-time bound is clipped with a visible marker; an empty verdict
    renders nothing. Non-human `text_authored_by` is labeled with its own
    AUTHORITY word (agent / mechanical — CHANNEL_AUTHORITY's vocabulary,
    cli._print_ruling's own label) even after human ratification — who
    wrote the words survives who approved them. Neither the authority
    suffix, the layer suffix, nor the cap counts the code-enforced classes
    any differently: a compact ruling is still one ruling against the cap."""
    mask = prose_mask(snap)
    rows = [r for r in active_rulings(
                project_dir, own=_own_read(snap))
            if str(r.get("verdict") or "").strip()]
    try:
        cap = config.ruling_cap()
    except Exception:
        return []
    shown, over = rows[:cap], rows[cap:]
    rendered_ids = {str(row.get("refutation_id")) for row in shown}
    manifest_all = _manifest_enforce_lines(project_dir, rendered_ids)
    if not shown and not manifest_all:
        return []
    # The manifest lines share the SAME cap as the ledger rows: they get
    # whatever room the ledger rows left, and anything past that room folds
    # into the over-cap count below rather than rendering uncapped.
    room = max(0, cap - len(shown))
    manifest_lines, manifest_over = manifest_all[:room], manifest_all[room:]
    total_over = len(over) + len(manifest_over)
    lines = [_RULING_HEADER]
    lines.extend(_inherited_notes(project_dir))
    policy_rendered = False
    for row in shown:
        suffix = _layer_suffix(row)
        compact, cls = _compact_line(row)
        if compact is not None:
            lines.append(compact + suffix)
            if cls == "policy":
                policy_rendered = True
            continue
        verdict = mask(str(row.get("verdict") or ""))
        if len(verdict) > refutations._MAX_RULING_TEXT:
            verdict = verdict[:refutations._MAX_RULING_TEXT] + "…"
        authored = row.get("text_authored_by")
        authored_suffix = (f"  [{authored}-written]"
                          if authored and authored != "human" else "")
        lines.append(f"{RULING_MARK} {verdict}{authored_suffix}{suffix}")
    if policy_rendered:
        lines.append(_POLICY_LEGEND)
    lines.extend(manifest_lines)
    if total_over:
        plural = "s" if total_over != 1 else ""
        lines.append(f"  (+{total_over} active ruling{plural} over cap — "
                     "daimon ruling list --inherited shows all)")
    return lines


# ---- #766 slice 5: the one-line decision count ------------------------------
#
# Registered as its own governed public string family — not a heading, not a
# panel: "N decisions waiting on you here (M elsewhere) - daimon decide".
# `here` is exactly what bare `daimon decide` lists (`pending.queue`);
# `elsewhere` is the sum of `pending.foreign_counts` — an INTEGER fold, never
# `foreign_queues` (the text path scar 0055 forbids on this surface). Frozen
# tokens: "decisions waiting on you", "here", "elsewhere", "- daimon decide".

_DECISION_COUNT_LINE_PLURAL = \
    "{n} decisions waiting on you here{elsewhere} - daimon decide"
_DECISION_COUNT_LINE_SINGULAR = \
    "1 decision waiting on you here{elsewhere} - daimon decide"


def decision_count_line(project_dir=None) -> str | None:
    """The #766 slice 5 count line, or None when there is nothing to say.

    `here` reads `pending.queue` (this project's own backlog); `elsewhere`
    reads `pending.foreign_counts`, SKIPPED ENTIRELY under
    `config.tenant_scoped()`: a tenant-scoped host must not learn that other
    buckets exist, so the call itself never happens, not merely its
    rendering. Zero-both is silence: both counts zero renders no line; a
    zero `here` with a nonzero `elsewhere` still renders. Fail-open like the
    panels around it, each half INDEPENDENTLY: a broken `here` read yields
    None (the line has nothing honest to say without it), but a broken
    `elsewhere` read degrades to 0 rather than discarding a perfectly good
    `here` count — one lane's failure must not blank the whole line.

    `project_dir` here follows `request_panel_lines`'s own gate, not the
    rulings section's: callers pass the CLI's `worldcheck_project`, so this
    line is absent on `--slug` and the global-pointer-fallback body — same
    posture as every panel in this family (this is content ABOUT another
    bucket's existence, just as a count, not a record read directly off it).

    The line's exact wording and scope are frozen; a change to either routes
    through the project's public-vocabulary process, not a local edit here.
    """
    if project_dir is None:
        return None
    try:
        here = len(pending.queue(project_dir=project_dir)["rows"])
    except Exception:
        return None
    elsewhere = 0
    notes: tuple = ()
    if not config.tenant_scoped():
        try:
            got = pending.foreign_counts_typed(project_dir=project_dir)
            elsewhere, notes = sum(got.counts.values()), got.notes
        except Exception:
            elsewhere = 0
    if here == 0 and elsewhere == 0:
        # #1132 PR 10a: other projects left out are said even when nothing
        # was counted (the note stands alone).
        return "\n".join(notes) or None
    suffix = f" ({elsewhere} elsewhere)" if elsewhere else ""
    template = (_DECISION_COUNT_LINE_SINGULAR if here == 1
                else _DECISION_COUNT_LINE_PLURAL)
    return "\n".join([template.format(n=here, elsewhere=suffix), *notes])


# ---- #694 PR 2: the recipient-side request panel ---------------------------

# Ruling-shaped (D2): always-present, skeleton furniture, capped and loud on
# overflow — never a section budget pressure is allowed to quietly thin out.
_REQUEST_PANEL_HEADER = "Requests waiting on you (from other projects):"

# An `ask` can be up to `requests._MAX_TEXT` (2000 chars); the panel line is
# meant to be skimmable, not the full record — `daimon request inbox` has the
# rest. Same display-cap idiom as `_truncate_agent_claim` above, and the
# reason the worst-case-cap test can bound the section's cost at all.
# #1127: the bound and the cut live in `requests.short_ask` so the decide
# headline and the hook inject lines share this single limit.
_REQUEST_ASK_CHARS = requests.ASK_CHARS


def _truncate_request_ask(ask: str) -> str:
    return requests.short_ask(ask)


class Card(NamedTuple):
    """One request card a panel printed: the id, whether showing it owes a
    `surfaced` / `verdict_surfaced` stamp row (decided on the row the panel
    itself rendered), and for a verdict card the late reply it showed."""
    request_id: str
    stamp: bool
    reply_event_id: str | None = None


def request_panel_lines(project_dir=None, *, mask=None) -> list[str]:
    """The lines of `request_panel`; see it."""
    return request_panel(project_dir, mask=mask)[0]


def request_panel(project_dir=None, *, mask=None):
    """The recipient-side panel as `(lines, cards)`: its rendered lines ([]
    when nothing is addressed to this project, or `project_dir` is None — the section is
    skeleton furniture, but empty furniture is noise).

    Fail-open like `ruling_lines`: ANY error from the composer yields []
    rather than costing the briefing. `project_dir` here is the CLI's
    `worldcheck_project` — the caller (render.render_brief /
    cli._render_briefing_body) is the ONE place that decides whether this
    request is same-project (D2's CLI-only gate); this function never
    inspects route or surface on its own.

    #961 slice 3: sourced from `requests.decision_renderable`, not
    `inbox_renderable` — this panel's own header calls it "Requests waiting
    on you", and a `kind == "info"` ask owes no accept, so it is not one of
    those any more. `decision_renderable` does the exclusion before its own
    cap, so a `work` ask never goes missing behind a newer `info` one; see
    its docstring for why. `inbox_renderable` itself has no production
    caller left as of review round 1: `request inbox` reads
    `requests.inbox_listing`, and the cards this returns are exactly the
    rows it printed, so the brief stamps `surfaced` for those and no other
    (stamping a card that was never actually shown would give `is_stale` a
    phantom anchor). `inbox_renderable` is kept as
    the unfiltered, every-kind composer several tests still exercise
    directly, and for a future consumer that wants every kind capped
    without the decision-only narrowing."""
    if project_dir is None:
        return [], ()
    try:
        entry = requests.decision_renderable(project_dir=project_dir)
    except Exception:
        return [], ()
    rows = entry.get("rows") or []
    # #1132 PR 10a: `sender-skipped`, the one note of the join, ends this
    # panel (and stands alone when every sender was left out).
    notes = list(entry.get("notes") or ())
    if not rows:
        return notes, ()
    mask = mask or (lambda text: text)
    lines = [_REQUEST_PANEL_HEADER]
    cards = []
    for row in rows:
        cards.append(Card(row["request_id"], requests.needs_surfaced_stamp(row)))
        # #961 slice 3: no `[info]` marker here any more — `decision_
        # renderable` already excludes `kind == "info"`, so every row this
        # loop sees is `work` by construction; the marker lives on
        # `owed_panel_lines` and the live-delivery lines below, which still
        # carry `info` rows and still need it.
        marker = "  [blocking]" if row.get("blocking") else ""
        # #978: read from the folded row only, placed after the truncated
        # ask so the id stays in its own span.
        claim_marker = "  [done claimed]" if row.get("done_pending") else ""
        lines.append(f"→ {row['request_id']}  "
                     f"{_truncate_request_ask(mask(row.get('ask', '')))}"
                     f"{marker}{claim_marker}")
        lines.append(f"  From: {row.get('from_label') or 'an unnamed project'}")
    overflow = entry.get("overflow") or 0
    if overflow:
        plural = "s" if overflow != 1 else ""
        lines.append(f"  (+{overflow} more waiting{plural} — "
                     "daimon request inbox)")
    lines.extend(notes)
    return lines, tuple(cards)


# ---- #885: the recipient-side owed panel ------------------------------------

# Registered and frozen the same way the two headings above are (§3 amendment
# 2026-08-27). The wording says OWE deliberately: an item here is not a
# decision waiting on the human, it is work this project already agreed to.
_OWED_PANEL_HEADER = "Requests you accepted and still owe:"


def owed_panel_lines(project_dir=None, *, mask=None) -> list[str]:
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
    notes = list(entry.get("notes") or ())
    if not rows:
        return notes
    mask = mask or (lambda text: text)
    lines = [_OWED_PANEL_HEADER]
    for row in rows:
        # #961 slice 2: same marker, same posture as `request_panel_lines`.
        kind_marker = "  [info]" if row.get("kind") == "info" else ""
        marker = "  [blocking]" if row.get("blocking") else ""
        lines.append(f"✓ {row['request_id']}  "
                     f"{_truncate_request_ask(mask(row.get('ask', '')))}"
                     f"{kind_marker}{marker}")
        lines.append(f"  From: {row.get('from_label') or 'an unnamed project'}")
    overflow = entry.get("overflow") or 0
    if overflow:
        lines.append(f"  (+{overflow} more owed — "
                     "daimon request inbox)")
    lines.append("  Close one with: daimon request done <id> --evidence …")
    lines.extend(notes)
    return lines


# ---- #694 PR 3: the sender-side verdict panel -------------------------------

_VERDICT_PANEL_HEADER = "Decisions on requests you sent:"

# Mirrors cli/request.py's _MARKS glyph-per-state — a separate small table
# rather than an import, because briefing.py must not depend on the cli
# package (the dependency runs the other way: cli imports briefing/render).
_VERDICT_MARKS = {"needs-info": "?", "accepted": "✓", "rejected": "×",
                  "done": "✔"}


def _mask_reply(row: dict, mask) -> dict:
    """The record with its latest reply's note masked, so the one reply line
    the panel prints can never carry a withheld value."""
    replies = row.get("replies") or []
    if not replies:
        return row
    latest = dict(replies[-1])
    latest["note"] = mask(latest.get("note") or "")
    return {**row, "replies": [*replies[:-1], latest]}


def verdict_panel_lines(project_dir=None, *, mask=None) -> list[str]:
    """The lines of `verdict_panel`; see it."""
    return verdict_panel(project_dir, mask=mask)[0]


def verdict_panel(project_dir=None, *, mask=None):
    """The sender-side panel as `(lines, cards)`: its rendered lines ([] when
    nothing this project sent has been decided yet, or `project_dir` is None). Same posture as
    `request_panel_lines`: fail-open, skeleton furniture, never silently
    truncated over RENDER_CAP. `project_dir` is the CLI's
    `worldcheck_project` — same D2 CLI-only gate, same caller contract.

    #961 slice 2: deliberately carries no `kind` marker. This panel reports
    the OUTCOME of a request this project itself sent, and it already chose
    the kind at open time — unlike the two panels above, nothing here is
    deciding how much scrutiny an ask deserves. Mirrors the same call made
    for `cli/request.py`'s `_verdict_inject_lines`."""
    if project_dir is None:
        return [], ()
    try:
        entry = requests.verdict_renderable(project_dir=project_dir)
    except Exception:
        return [], ()
    rows = entry.get("rows") or []
    notes = list(entry.get("notes") or ())
    if not rows:
        return notes, ()
    mask = mask or (lambda text: text)
    lines = [_VERDICT_PANEL_HEADER]
    cards = []
    for row in rows:
        # #1117: one stamp row carries whichever of the epoch and the late
        # reply the brief shows.
        reply_id = requests.unseen_reply_id(row)
        cards.append(Card(row["request_id"],
                          bool(requests.needs_verdict_surfaced_stamp(row)
                               or reply_id), reply_id))
        state = str(row.get("state") or "")
        mark = _VERDICT_MARKS.get(state, "?")
        # #961 slice 3: an agent-landed accept reads distinctly from a human
        # one on the surface that reports the OUTCOME of an ask this
        # project sent — unlike the `kind` marker slice 2 deliberately left
        # off this panel (the sender already chose the kind), WHO accepted
        # is new information the sender has not seen yet. Read from the
        # folded row's own `accepted_by`, never derived here.
        state_label = ("accepted (by agent)"
                       if state == "accepted" and row.get("accepted_by") == "agent"
                       else state)
        lines.append(f"{mark} {state_label}  {row['request_id']}  "
                     f"{_truncate_request_ask(mask(row.get('ask', '')))}")
        lines.append(f"  To: {row.get('to') or '?'}")
        note = str(row.get("note") or "").strip()
        if note:
            lines.append(f"  Note: {_truncate_request_ask(mask(note))}")
        done_evidence = str(row.get("done_evidence") or "").strip()
        if done_evidence:
            lines.append(
                f"  Done: {_truncate_request_ask(mask(done_evidence))}")
        # #1117: ONE capped line (skeleton the trimmer keeps).
        reply_line = requests.latest_reply_line(_mask_reply(row, mask))
        if reply_line:
            lines.append(f"  {reply_line}")
    overflow = entry.get("overflow") or 0
    if overflow:
        plural = "s" if overflow != 1 else ""
        lines.append(f"  (+{overflow} more decided{plural} — "
                     "daimon request list)")
    lines.extend(notes)
    return lines, tuple(cards)


def drop_repeated_notes(*blocks) -> list:
    """The panel blocks with each warning line kept once, at its first
    appearance: the three request panels read the same joins, so a sender
    left out would otherwise be said up to three times in one brief."""
    seen: set = set()
    out = []
    for block in blocks:
        kept = []
        for line in block:
            if line.startswith(WARNING_MARK):
                if line in seen:
                    continue
                seen.add(line)
            kept.append(line)
        out.append(kept)
    return out


def render_plain(b: dict, degraded: bool = False, rulings=(),
                 request_lines=(), verdict_lines=(), owed_lines=(),
                 decision_count: str | None = None, *,
                 max_bytes: int | None = None,
                 loops_pointer: bool = True, notes=()) -> str:
    """The deterministic briefing text: a thin wrapper over `select` and
    `render_selection` (#1128), kept so its many call sites keep working.

    Under budget this is the full briefing (the decision cap aside). Over it,
    long non-verbatim items shorten first (#30: verbatim is never rewritten),
    then whole candidates drop in the one global order `select` documents,
    each section announcing what it lost. `degraded` (#204) downgrades every
    verbatim label and adds one header note when the receipt is unverifiable.
    `decision_count` (#766 slice 5) and the request/verdict/owed panels are
    protected furniture.

    One budget: `effective_budget(max_bytes)`, min(brief_max_bytes,
    brief_max_tokens * 4) in UTF-8 bytes, the unit the hosts that spill
    measure (#1044). `max_bytes` overrides the byte side for this call only;
    `render.render_brief` passes the room left once the blocks printed beside
    the body are counted."""
    budget = effective_budget(max_bytes)
    sel = select(b, budget, degraded=degraded, rulings=rulings,
                 request_lines=request_lines, verdict_lines=verdict_lines,
                 owed_lines=owed_lines, decision_count=decision_count,
                 loops_pointer=loops_pointer, notes=notes)
    text = render_selection(sel)
    _log_render_size(text, budget)
    return text



# Printed in place of the body when the view is CLOSED (the trust ledger cannot
# be read, so no item can be proven safe to show): the greeting, the ledger
# note and the standing furniture still render, and this line says why the
# items do not.
CLOSED_LINE = ("(daimon: this project's items are withheld while a ledger "
               "above cannot be read.)")


def _head_lines(degraded: bool, rulings, notes=()) -> list[str]:
    """The greeting, the #204 degrade note, the ledger-health notes (#1132
    PR 7a: each already starts with a warning sign) and the standing rulings
    block:
    the portion of the render that comes before decision/request/verdict/owed
    panels and the cognitive body. #1044's byte ceiling protects exactly this
    prefix: it is the closest this render gets to a human-ratified constraint
    (rulings) plus the one line every render opens with, and the ceiling must
    never silently eat either. Shared with `render_selection` so the two can
    never drift apart on what "the head" is."""
    parts = [GREETING]
    if degraded:
        # One header note (#204), embedded in the text so the hook-injected
        # briefing carries it too — not just the human-facing CLI render.
        parts.append("")
        parts.append(DEGRADE_NOTE)
    if notes:
        # Charged with the head like the rulings: never a drop candidate, and
        # a machine reader keeps every line as a warning because each starts
        # with the warning sign (the greeting stays the header).
        parts.append("")
        parts.extend(notes)
    if rulings:
        # #693: skeleton furniture at the top, never a drop candidate — the
        # budget loops re-render with the same lines and can only trim the
        # sections below.
        parts.append("")
        parts.extend(rulings)
    return parts


# ---- #1128: one ranked budget — select, then render ----
#
# The briefing used to have a per-section drop order, an unexplained
# never-trimmed set, and a text-level backstop that knew about neither. This
# is the replacement allocation model (design: "Briefing Budget Allocation
# (#1128)", revision 3):
#   * `select` is a PURE function of (annotated items, budget, now). It
#     returns a `Selection`: what is kept, what is dropped, and WHY.
#   * `render_selection` turns a Selection into text; plain and rich output
#     share its section order and its notes.
#   * One byte budget, min(brief_max_bytes, brief_max_tokens * 4).

# Reader-need order, shared by the plain and rich renders (one constant, so
# the two can never drift). Decisions sit above VERIFY in EVERY render.
SECTION_ORDER = ("decisions", "external", "open_loops", "beliefs",
                 "uncertainties", "active_topic", "contradictions")

# The one hand-written map from a presentation section to the checkpoint field
# that feeds it: (section, key) of schema.ITEM_FIELDS. build() splits ONE
# field (open_questions) into "external" and "open_loops" by the
# external_state flag, so both name it. Everything keyed by section that a
# field row can answer is derived from this (and a census test pins that every
# field is reached and every briefable one maps into SECTION_ORDER).
SECTION_FIELD = {
    "decisions": ("working_context", "recent_decisions"),
    "external": ("working_context", "open_questions"),
    "open_loops": ("working_context", "open_questions"),
    "beliefs": ("epistemic_snapshot", "strong_beliefs"),
    "uncertainties": ("epistemic_snapshot", "uncertainties"),
    "active_topic": ("working_context", "active_topic"),
    "contradictions": ("epistemic_snapshot", "contradictions_flagged"),
}

_FIELD_OF = {(f.section, f.key): f for f in schema.ITEM_FIELDS}


def _section_field(section: str) -> schema.ItemField:
    return _FIELD_OF[SECTION_FIELD[section]]


# The item-bearing sections: SECTION_ORDER minus the singleton's section.
_ITEM_SECTIONS = tuple(s for s in SECTION_ORDER
                       if not _section_field(s).singleton)

# build()'s section keys that render a resolve handle — the single source of
# truth both render paths (plain _line below, rich render._rich_brief) and
# `daimon loops` key off of: the sections whose field is briefable.
# Decisions/beliefs/contradictions are valid `daimon resolve` targets too
# (resolve accepts any item id), but are not loop-shaped: stamping a handle
# there would invite resolving settled facts, which is out of this slice's
# scope.
BRIEFABLE_SECTIONS = frozenset(s for s in SECTION_ORDER
                               if _section_field(s).briefable)

SECTION_HEADERS = {
    "decisions": "Decisions made:",
    "external": f"{VERIFY_PHRASE} (state may have changed outside "
                "this session):",
    "open_loops": "Open loops:",
    "beliefs": "Beliefs held:",
    "uncertainties": "Was uncertain about:",
    "contradictions": "Contradictions flagged:",
}

# #78 weights are only ever compared INSIDE a section, never across types.
# A field with no scoring type (contradictions_flagged) falls back to its kind
# word, which is not a TYPE_RULES key, so scoring resolves it to the default
# rules exactly as the hand-kept table did.
_WEIGHT_TYPE = {s: _section_field(s).scoring_type or _section_field(s).kind
                for s in _ITEM_SECTIONS}

# Background content goes before actionable content within a tier.
_BACKGROUND = frozenset({"beliefs", "uncertainties"})

# Where a hidden-items note points. beliefs/uncertainties have no listing
# command, so their notes carry no pointer. `daimon loops --stale` lists what
# the stale rule hid (the `prepare` stale set); a note that also lost items
# for plain budget points at the whole listing, whose rows carry an age.
_NOTE_POINTER = {"external": "daimon loops", "open_loops": "daimon loops"}
_STALE_POINTER_SUFFIX = " --stale"

# When two candidates tie on every key above (equal percentile), the section
# the reader needs least goes first: the old drop order, kept as the tie-break.
_TIE_SECTION_RANK = {"beliefs": 0, "uncertainties": 1, "decisions": 2,
                     "open_loops": 3, "external": 4, "contradictions": 5}

_DECISION_FLOOR = 3
_TEAMMATE_FLOOR = 2
# The panels in print order, with the command that lists what a collapsed
# panel hid.
_PANEL_POINTERS = ("daimon request inbox", "daimon request list",
                   "daimon request inbox")


def _decision_floor(cap: int) -> int:
    """How many of the newest native decisions are protected: min(3, cap).
    cap == 0 is unbounded, so the plain 3. Protected because the decisions the
    session just made are what a resumed session most needs, and a budget that
    is spent on older carried items must never be allowed to eat them."""
    return _DECISION_FLOOR if not cap else min(_DECISION_FLOOR, cap)


def _is_flagged(item) -> bool:
    """An item carrying an ACTION flag: the reader has something to do about
    it (confirm or reject a claim, a supersede, an amendment, a state change
    worldcheck saw). Flagged items form the top candidate tier: they drop
    last among candidates and never silently. `_worldcheck_confirmed` is not
    a flag, it asks nothing of the reader."""
    if not isinstance(item, dict):
        return False
    wc = item.get("_worldcheck")
    return bool(item.get("_agent_claim") or item.get("_supersede_candidate")
                or item.get("_amend")
                or (isinstance(wc, dict) and wc.get("note")))


def _stale_days_of(item):
    value = item.get("_stale_carried_days") if isinstance(item, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _item_age_days(item, now) -> float:
    """Age for the drop key's tie-break only. A stamped stale age wins; else
    first_seen; no parseable stamp (or a future one) is age 0, never an
    error."""
    stale = _stale_days_of(item)
    if stale is not None:
        return stale
    epoch = store._created_epoch(item.get("first_seen"))
    if epoch is None:
        return 0.0
    return max(0.0, (now - epoch) / 86400.0)


def _rank_pcts(items, section, now) -> list[float]:
    """Within-section rank percentile of each item's #78 effective weight,
    0.0 = lightest. Ties share a percentile, so equal weights fall through to
    the age and ordinal keys instead of an arbitrary order."""
    weights = [scoring.effective_weight(i, _WEIGHT_TYPE[section], now)
               for i in items]
    n = len(weights)
    return [sum(1 for other in weights if other < w) / n for w in weights]


class Selection:
    """What `select` decided. `kept` maps each section to its kept items
    (active_topic to its item or None); `dropped` and `reasons` are parallel
    per-section lists, a reason being "cap" (over the decision cap) or
    "budget". `order` is every droppable candidate in drop order, so the
    dropped-for-budget candidates are always a prefix of it. The rest is what
    `render_selection` needs to print the same thing `select` measured."""

    def __init__(self, kept, dropped, reasons, *, order, totals, cap,
                 stale_days, budget, degraded, rulings, count_line, panels,
                 overage, now, panel_names=(), reserved=0,
                 teammate_blocks=(), teammate_header="", kept_teammates=(),
                 collapsed=False, loops_pointer=True, full_quotes=False,
                 notes=(), cards=None):
        self.kept = kept
        self.notes = tuple(notes)
        self.dropped = dropped
        self.reasons = reasons
        self.order = order
        self.totals = totals
        self.cap = cap
        self.stale_days = stale_days
        self.budget = budget
        self.degraded = degraded
        self.rulings = rulings
        self.count_line = count_line
        self.panels = panels
        self.overage = overage
        self.now = now
        # parallel to `panels`: "request" / "verdict" / "owed". The CLI reads
        # what was printed from here, so `surfaced` follows the print.
        self.panel_names = list(panel_names)
        # the panels printed with their cards, i.e. not collapsed to a count
        self.panel_names_whole = [] if collapsed else list(panel_names)
        # Bytes the caller prints beside the body (HANDOFF, version note,
        # drift block, withheld note), charged to the same budget.
        self.reserved = reserved
        self.teammate_blocks = list(teammate_blocks)
        self.teammate_header = teammate_header
        self.kept_teammates = list(kept_teammates)
        # False on any route where `daimon loops` would list a DIFFERENT
        # project than the one briefed (global fallback, --slug): the note
        # then carries no pointer at all.
        self.loops_pointer = loops_pointer
        # The opt-in LLM path must reproduce verbatim quotes whole, so its
        # sizing charges them whole; the deterministic render shows a span.
        self.full_quotes = full_quotes
        # #1132 PR 7b-2: the request and verdict cards captured by the one
        # read that built the panel lines, {"request": (Card, ...), "verdict":
        # (Card, ...)}. What the brief stamps as surfaced follows from here.
        self.cards = {"request": (), "verdict": (), **(cards or {})}
        self._lines: dict = {}

    def printed_cards(self) -> dict:
        """The cards of the panels printed whole: a panel collapsed to a
        count line showed no card, so none of its cards is owed a stamp."""
        return {name: (cards if name in self.panel_names_whole else ())
                for name, cards in self.cards.items()}

    def dropped_for(self, section, reason=None):
        return [i for i, r in zip(self.dropped.get(section, []),
                                  self.reasons.get(section, []))
                if reason is None or r == reason]


def effective_budget(max_bytes=None) -> int | None:
    """The ONE byte budget: min(brief_max_bytes, brief_max_tokens * 4). The
    token figure is a chars//4 estimate and bytes are never fewer than chars,
    so a single byte figure is never looser than the token budget it replaces.
    0 or None on a side means that side is unbounded; both unbounded is None.
    `max_bytes` overrides the byte side for one call (a caller that prints
    other blocks beside the body passes the room it has left)."""
    byte_side = config.brief_max_bytes() if max_bytes is None else max_bytes
    token_side = config.brief_max_tokens() * 4
    sides = [s for s in (byte_side, token_side) if s]
    return min(sides) if sides else None


def _collapse_panel(lines, pointer) -> list[str]:
    """A request/verdict/owed panel reduced to its frozen header and one
    count line. Cards are the unindented lines after the header."""
    cards = sum(1 for ln in lines[1:] if ln and not ln.startswith(" "))
    return [lines[0], f"  ({cards} cut for budget, see: {pointer})"]


def _split_decisions(decisions, cap, now):
    """Apply the decision cap (the per-section limit that used to live in
    build): this session's own decisions first (the chronological tail), the
    room left to carried ones by #78 weight. Returns (kept_entries,
    capped_entries), each a list of (index, item). cap == 0 is unbounded."""
    entries = list(enumerate(decisions))
    native = [(i, d) for i, d in entries if not _is_carried(d)]
    carried = [(i, d) for i, d in entries if _is_carried(d)]
    if not cap or len(decisions) <= cap:
        return native + carried, []
    kept_native = native[-cap:]
    room = cap - len(kept_native)
    kept_carried = []
    if room > 0:
        ranked = sorted(carried, key=lambda e: scoring.effective_weight(
            e[1], "recent_decision", now), reverse=True)
        kept_carried = ranked[:room]
    kept = kept_native + kept_carried
    kept_idx = {i for i, _ in kept}
    capped = [(i, d) for i, d in entries if i not in kept_idx]
    return kept, capped


def select(b: dict, budget, now=None, *, degraded: bool = False, rulings=(),
           request_lines=(), verdict_lines=(), owed_lines=(),
           decision_count: str | None = None, reserved: int = 0,
           teammate_blocks=(), teammate_header: str = "",
           loops_pointer: bool = True, full_quotes: bool = False,
           notes=(), cards=None) -> Selection:
    """#1128: decide what the briefing shows. Pure: a function of the
    annotated items in `b`, the byte `budget` (None = unbounded: the decision
    cap still applies, nothing else is dropped) and `now`.

    Protected, charged first and never dropped (they give way only in a fixed
    order when they alone exceed the budget, see below): the greeting, the
    degrade note, the ledger-health `notes`, standing rulings, the decision count and the
    request/verdict/owed panels (each bounded by its own render cap), the
    active topic, the `min(3, cap)` newest native decisions, and the
    per-section count lines. Everything else is a candidate. Candidates drop
    lowest key first, whole items only, in one global order:
      1. action-flagged items last (they ask something of the reader);
      2. stale carried before everything else;
      3. background sections (beliefs, uncertainties) before actionable ones;
      4. carried before native;
      5. within-section #78 weight percentile, lightest first;
      6. on a tie, the section the reader needs least (the old drop order),
         then older first, then position, so output is deterministic.

    Over-budget protected set, in this order: panels collapse to count lines,
    the decision floor drops to one, then one marker line names the overage.
    Greeting, degrade note and rulings are never cut.

    One allocator for everything `daimon brief` prints: `budget` bounds the
    body, `reserved` bytes (HANDOFF, version note, drift block, withheld note,
    never cut) and the teammates block together. `teammate_blocks` are the
    pre-rendered per-teammate texts, newest first; the first two are protected
    (a floor of two, plus the header) and the rest are candidates in the
    background class, dropped before anything actionable and announced.

    Quote length (#1129): the deterministic render shows an evidence quote
    as a bounded span (`display.quote_span`), so that is what it costs. The
    opt-in LLM path must reproduce verbatim quotes whole, so it passes
    `full_quotes=True` and is charged the stored quote. The two paths differ
    in quote length on purpose; this stays pure, no config read."""
    if now is None:
        now = b.get("now") if isinstance(b.get("now"), (int, float)) \
            else time.time()
    cap = config.max_briefing_decisions()
    stale_days = config.stale_days()
    dropped: dict[str, list] = {s: [] for s in _ITEM_SECTIONS}
    reasons: dict[str, list] = {s: [] for s in _ITEM_SECTIONS}
    totals = {}
    entries = {}
    for s in _ITEM_SECTIONS:
        raw = [i for i in (b.get(s) or []) if isinstance(i, dict)]
        totals[s] = len(raw)
        entries[s] = list(enumerate(raw))
    kept_entries, capped_entries = _split_decisions(
        [it for _, it in entries["decisions"]], cap, now)
    for _, d in capped_entries:
        dropped["decisions"].append(d)
        reasons["decisions"].append("cap")
    entries["decisions"] = kept_entries

    floor_n = _decision_floor(cap)
    native_kept = [(i, d) for i, d in kept_entries if not _is_carried(d)]
    floor_order = [i for i, _ in native_kept[-floor_n:]] if floor_n else []
    floor_ids = set(floor_order)

    # Candidates, with their drop key. `ordinal` keeps the order stable: a
    # section's earlier items drop first for decisions (chronological, oldest
    # first), its tail first elsewhere (sorted heaviest-first by build()).
    cands: list[tuple] = []
    for s in _ITEM_SECTIONS:
        pool = [(i, it) for i, it in entries[s]
                if not (s == "decisions" and i in floor_ids)]
        if not pool:
            continue
        pcts = _rank_pcts([it for _, it in pool], s, now)
        for (idx, it), pct in zip(pool, pcts):
            ordinal = idx if s == "decisions" else -idx
            key = (1 if _is_flagged(it) else 0,
                   0 if _stale_days_of(it) is not None else 1,
                   0 if s in _BACKGROUND else 1,
                   0 if _is_carried(it) else 1,
                   pct,
                   _TIE_SECTION_RANK[s],
                   -_item_age_days(it, now),
                   ordinal,
                   SECTION_ORDER.index(s))
            cands.append((key, s, idx, it))
    blocks = list(teammate_blocks)
    floor_teammates = min(_TEAMMATE_FLOOR, len(blocks))
    for ti in range(floor_teammates, len(blocks)):
        # Background class, lightest of all: later (older) teammates first.
        cands.append(((0, 1, 0, 1, -1.0, -1, 0.0, -ti, len(SECTION_ORDER)),
                      "teammates", ti, None))
    cands.sort(key=lambda c: c[0])

    active = b.get("active_topic")
    active = active if isinstance(active, dict) else None
    line_cache: dict = {}
    panel_sets = [(list(request_lines), _PANEL_POINTERS[0], "request"),
                  (list(verdict_lines), _PANEL_POINTERS[1], "verdict"),
                  (list(owed_lines), _PANEL_POINTERS[2], "owed")]

    def build_selection(n_dropped, floor_keep, collapse, overage=0):
        seq = cands
        if floor_keep < floor_n:
            # The floor gave way, so every older decision candidate goes
            # before any other candidate: a newer decision never gives way
            # to an older one.
            seq = sorted(cands, key=lambda c: 0 if c[1] == "decisions" else 1)
        drop_ids = {(s, idx) for _, s, idx, _ in seq[:n_dropped]}
        floor_cut = set(floor_order[:max(0, len(floor_order) - floor_keep)])
        kept = {}
        d = {s: list(dropped[s]) for s in _ITEM_SECTIONS}
        r = {s: list(reasons[s]) for s in _ITEM_SECTIONS}
        for s in _ITEM_SECTIONS:
            keep = []
            for idx, it in entries[s]:
                if (s, idx) in drop_ids or (s == "decisions"
                                            and idx in floor_cut):
                    d[s].append(it)
                    r[s].append("budget")
                else:
                    keep.append(it)
            kept[s] = keep
        kept["active_topic"] = active
        panels = [_collapse_panel(lines, ptr) if collapse else lines
                  for lines, ptr, _ in panel_sets if lines]
        names = [name for lines, _, name in panel_sets if lines]
        kept_team = [ti for ti in range(len(blocks))
                     if ("teammates", ti) not in drop_ids]
        sel = Selection(kept, d, r,
                        order=[(s, it) for _, s, _, it in seq],
                        totals=totals, cap=cap,
                        stale_days=stale_days, budget=budget,
                        degraded=degraded, rulings=list(rulings),
                        count_line=decision_count, panels=panels,
                        overage=overage, now=now, panel_names=names,
                        reserved=reserved, teammate_blocks=blocks,
                        teammate_header=teammate_header,
                        kept_teammates=kept_team, collapsed=collapse,
                        loops_pointer=loops_pointer, full_quotes=full_quotes,
                        notes=notes, cards=cards)
        sel._lines = line_cache  # the search below re-renders many times
        return sel

    full = len(cands)
    if budget is None:
        return build_selection(0, floor_n, False)

    def size(sel):
        # Everything printed: the body, the blocks beside it, the teammates.
        return (_byte_len(render_selection(sel)) + reserved
                + _byte_len(teammates_text(sel)))

    # Stage 1 (kept from #79): shorten monster non-verbatim items before any
    # item is dropped; verbatim text is never rewritten (#30).
    if size(build_selection(0, floor_n, False)) > budget:
        for s in _ITEM_SECTIONS:
            entries[s] = [
                (idx, it if it.get("trust") == "verbatim" else
                 {**it, "text": truncate_preserving_sections(
                     it.get("text", ""), _ITEM_TRUNCATE_CHARS),
                  # #1129: decided against the text as stored
                  "_quote_in_text": bool(it.get("quote")) and not
                  display.quote_span(it.get("quote"), it.get("text"))})
                for idx, it in entries[s]]
        lookup = {s: dict(entries[s]) for s in _ITEM_SECTIONS}
        cands = [(k, s, idx, it if s == "teammates" else lookup[s][idx])
                 for k, s, idx, it in cands]

    # Pick the protection level: full panels and the full floor; collapsed
    # panels; floor of one. The first that fits with EVERY candidate dropped
    # wins, and candidates then keep as many as fit.
    levels = [(floor_n, False), (floor_n, True), (min(1, floor_n), True)]
    for floor_keep, collapse in levels:
        if size(build_selection(full, floor_keep, collapse)) <= budget:
            # A lowered floor means every older decision goes first, whole:
            # a newer decision never gives way to an older one.
            start = (sum(1 for c in cands if c[1] == "decisions")
                     if floor_keep < floor_n else 0)
            for n_dropped in range(start, full + 1):
                sel = build_selection(n_dropped, floor_keep, collapse)
                if size(sel) <= budget:
                    return sel
    floor_keep, collapse = levels[-1]
    base = build_selection(full, floor_keep, collapse)
    over = size(base) - budget
    return build_selection(full, floor_keep, collapse, overage=max(over, 1))


def section_note(sel: Selection, section: str) -> str | None:
    """The one note line for a section that lost items, generated from the
    manifest alone (so its counts cannot disagree with it). None when
    nothing was lost. Shared by the plain render, the rich render and the
    teammates block."""
    lost = sel.dropped.get(section) or []
    if not lost:
        return None
    reasons = sel.reasons[section]
    shown = len(sel.kept.get(section) or [])
    total = shown + len(lost)
    flagged = sum(1 for i in lost if _is_flagged(i))
    if section == "decisions":
        native_budget = sum(1 for i, r in zip(lost, reasons)
                            if r == "budget" and not _is_carried(i))
        native_cap = sum(1 for i, r in zip(lost, reasons)
                         if r == "cap" and not _is_carried(i))
        older = sum(1 for i in lost if _is_carried(i))
        parts = []
        if native_budget:
            parts.append(f"{native_budget} from the last session cut for budget")
        if native_cap:
            parts.append(f"{native_cap} over the {sel.cap}-item cap")
        if older:
            parts.append(f"{older} older not shown")
    else:
        stale = sum(1 for i in lost if _stale_days_of(i) is not None)
        other = len(lost) - stale
        noun = "checks" if section == "external" else "items"
        parts = []
        if stale:
            parts.append(f"{stale} carried {noun} unverified over "
                         f"{sel.stale_days:g}d hidden")
        if other:
            parts.append(f"{other} cut for budget")
    body = f"{shown} of {total} shown; " + ", ".join(parts)
    if flagged:
        body += f"; {flagged} flagged item{'s' if flagged != 1 else ''} hidden"
    pointer = _NOTE_POINTER.get(section) if sel.loops_pointer else None
    if pointer:
        if stale and not other:
            pointer += _STALE_POINTER_SUFFIX
        body += f". See: {pointer}"
    return f"  ({body})"


def teammates_text(sel: Selection) -> str:
    """The Teammates block for a Selection: the header, the kept per-teammate
    blocks, and one line saying how many were left out for budget. "" when
    there are no teammates. Each block starts with a blank line, so the
    composed text is byte-identical to the old single-pass formatter when
    nothing is dropped."""
    if not sel.teammate_blocks:
        return ""
    out = "\n" + sel.teammate_header
    out += "".join(sel.teammate_blocks[i] for i in sel.kept_teammates)
    lost = len(sel.teammate_blocks) - len(sel.kept_teammates)
    if lost:
        out += (f"\n\n  ({lost} more teammate{'s' if lost != 1 else ''} "
                "not shown for budget)")
    return out + "\n"


def _sel_line(sel: Selection, section: str, item) -> str:
    key = id(item)
    line = sel._lines.get(key)
    if line is None:
        line = _line(item, sel.degraded, section in BRIEFABLE_SECTIONS,
                     sel.full_quotes)
        sel._lines[key] = line
    return line


def render_selection(sel: Selection) -> str:
    """The deterministic briefing text for a Selection. The head (greeting,
    degrade note, rulings) comes first, then the decision count and the
    panels, then the sections in `SECTION_ORDER`, each followed by its note
    when it lost items; a final marker line when the protected set alone was
    over budget."""
    parts = _head_lines(sel.degraded, sel.rulings, sel.notes)
    if sel.count_line:
        parts.append("")
        parts.append(sel.count_line)
    for panel in sel.panels:
        parts.append("")
        parts.extend(panel)
    for section in SECTION_ORDER:
        if section == "active_topic":
            if sel.kept.get("active_topic"):
                parts.append("")
                parts.append("Active topic: "
                             + sel.kept["active_topic"].get("text", "").strip())
            continue
        items = sel.kept.get(section) or []
        note = section_note(sel, section)
        if not items and not note:
            continue
        parts.append("")
        parts.append(SECTION_HEADERS[section])
        parts.extend(_sel_line(sel, section, i) for i in items)
        if note:
            parts.append(note)
    if sel.overage:
        parts.append("")
        parts.append(f"(daimon: the fixed sections alone are {sel.overage} "
                     f"bytes over the {sel.budget}-byte budget; "
                     "DAIMON_BRIEF_MAX_BYTES raises it)")
    return "\n".join(parts)


def _iter_trusted_quotes(checkpoint):
    """Yield every verbatim item's quote across the cognitive sections.
    Sections come from schema.ITEM_FIELDS so a field added there is validated
    here without another hand-kept list (#146 drift class; #161 added the
    active_topic singleton this way)."""
    for _field, item in schema.iter_items(checkpoint):
        if (item.get("trust") == "verbatim"
                and str(item.get("quote") or "").strip()):
            yield str(item["quote"]).strip()


def _kept_checkpoint(sel: Selection) -> dict:
    """A checkpoint-shaped view of ONLY what the Selection kept (#1128), the
    one thing the LLM briefing path is allowed to narrate or be validated
    against. Item dicts are passed through untouched."""
    kept = sel.kept
    return {
        "working_context": {
            "active_topic": kept.get("active_topic"),
            "open_questions": list(kept.get("external") or [])
            + list(kept.get("open_loops") or []),
            "recent_decisions": list(kept.get("decisions") or []),
        },
        "epistemic_snapshot": {
            "strong_beliefs": list(kept.get("beliefs") or []),
            "uncertainties": list(kept.get("uncertainties") or []),
            "contradictions_flagged": list(kept.get("contradictions") or []),
        },
    }


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


def render(checkpoint: dict, project_dir=None, worldcheck_project=None,
           loops_pointer: bool = True, snap=None,
           notes=(), cards_out: dict | None = None) -> str | None:
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
    own `worldcheck_project`, D2).

    `cards_out`, when given, receives the request and verdict cards the same
    read printed (both panels print whole here), for the caller's stamps."""
    b = build(checkpoint)
    # #1132 PR 7a: `snap` is the view's snapshot (the rulings are read once
    # out of it and every panel masks a withheld whole value); `notes` are its
    # ledger-health lines. Neither given: the legacy behavior, nothing masked.
    mask = prose_mask(snap)
    rulings = (ruling_lines(project_dir, snap=snap)
               if project_dir is not None else [])
    # #766 slice 5: same gate as the request panel below, not the rulings
    # section above — this line is content ABOUT another bucket's existence
    # (a count), so it follows request_panel_lines's posture: absent on
    # --slug and the global-pointer-fallback body.
    decision_count = (decision_count_line(worldcheck_project)
                      if worldcheck_project is not None else None)
    decision_count_block = [decision_count] if decision_count else []
    request_lines, request_cards = request_panel(worldcheck_project, mask=mask)
    verdict_lines, verdict_cards = verdict_panel(worldcheck_project, mask=mask)
    owed_lines = (owed_panel_lines(worldcheck_project, mask=mask)
                  if worldcheck_project is not None else [])
    request_lines, verdict_lines, owed_lines = drop_repeated_notes(
        request_lines, verdict_lines, owed_lines)
    if cards_out is not None:
        # Both panels print whole on this path: the caller stamps from these.
        cards_out.update(request=request_cards, verdict=verdict_cards)
    closed = snap is not None and snap.closed
    head = [GREETING] if closed else []
    skeleton_blocks = [blk for blk in (head, list(notes), rulings,
                                       decision_count_block,
                                       request_lines, verdict_lines,
                                       owed_lines) if blk]
    if b is None:
        # #693/#694/#766: a ruling ratified, a request addressed, a verdict
        # decided, or a decision now waiting — before the first real
        # checkpoint (a day-one action) — must still reach context;
        # "nothing worth surfacing" is no longer true when any of these exist.
        if not skeleton_blocks:
            return None
        if closed:
            skeleton_blocks.append([CLOSED_LINE])
        return "\n\n".join("\n".join(blk) for blk in skeleton_blocks)
    degraded = receipt_degraded(checkpoint)
    if config.llm_briefing():
        # #1128: the LLM narrates only what `select` kept under the same
        # budget the deterministic render uses (the rulings and panels it is
        # handed are charged as protected furniture), and the verbatim-quote
        # check runs over that same set, so this path obeys the same model.
        sel = select(b, effective_budget(), degraded=degraded,
                     rulings=rulings, request_lines=request_lines,
                     verdict_lines=verdict_lines, owed_lines=owed_lines,
                     decision_count=decision_count,
                     loops_pointer=loops_pointer, full_quotes=True,
                     notes=notes)
        kept_checkpoint = _kept_checkpoint(sel)
        rendered = _render_llm(kept_checkpoint)
        if rendered:
            if _validate_llm_render(rendered, kept_checkpoint):
                # The LLM render carries no per-item marks to degrade; the one
                # header note stays FIRST on every path (#204 — the loudest
                # line never moves below another section). #693/#694: the LLM
                # narrates the CHECKPOINT; rulings and both request panels
                # live outside it, so the deterministic sections are
                # prepended verbatim after the note — never re-narrated,
                # never trusted to a generative pass.
                parts = ([DEGRADE_NOTE] if degraded else [])
                # `notes` ride in skeleton_blocks, first
                parts.extend("\n".join(blk) for blk in skeleton_blocks)
                parts.append(rendered)
                return "\n\n".join(parts)
            log.warning("llm briefing dropped a verbatim quote — "
                        "falling back to the deterministic render")
    return render_plain(b, degraded, rulings, request_lines, verdict_lines,
                        owed_lines, decision_count,
                        loops_pointer=loops_pointer, notes=notes)


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
