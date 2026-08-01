"""The ONE capture pipeline (#432): serialize -> stamp -> carry+fold ->
bind_links -> supersede emission -> corroboration emission -> write ->
rejection ledger.

Both capture entry points — `cli._run_serialize` and `hooks.on_session_end` —
call `run`. They had drifted: the hook wrote checkpoints that skipped the
CLI-layer steps (created/transcript_hash stamps, carry.merge with the
resolved fold, bind_links, supersede-candidate emission with its #425
forgotten-filter, and the #376 rejection ledger), so what a checkpoint
contained depended on which door the session left through.
`tests/test_capture_parity.py` is the standing guard: any re-divergence
fails CI.

Error posture stays at the CALLER: this module raises `serializer`'s
TooShortError / SerializeError (and anything the store raises) upward —
the hook wraps everything in its never-raise contract, the CLI prints and
exits. The advisory sub-steps (carry, rejection ledger) fail open HERE,
identically for both doors, because a broken advisory feature must never
cost the checkpoint itself.

Intended per-door differences are explicit parameters, nothing else:
  - `escalate` (#360): only `daimon heal` may pass True — escalation cost
    scales with failure, not usage. The hook and plain `serialize` never do.
  - `transcript_path=None`: some hosts hand the hook no transcript FILE
    (messages arrive via transcript.from_session), so the session-end
    `created` stamp (#123) has no source and store.write_checkpoint's
    setdefault-now covers it. A host constraint, not pipeline drift —
    whenever a path exists, both doors stamp identically.
"""

import logging
import time
from pathlib import Path

from . import carry, config, normalize, serializer, store, transcript

log = logging.getLogger(__name__)


def run(session_id: str, messages, *, project, chat, deadline,
        transcript_path=None, transcript_sha=None,
        escalate: bool = False) -> Path | None:
    """Serialize `messages` into a checkpoint routed to `project` (used AS-IS;
    None => global pointer only — the caller decides routing, same contract
    as the old cli._run_serialize).

    Returns store.write_checkpoint's result: the checkpoint path, or None
    when the write boundary refused (kill switch, #421) — each caller renders
    its own skip/success line. Serializer failures propagate."""
    checkpoint = serializer.serialize_strict(
        session_id, messages, chat=chat, deadline=deadline, escalate=escalate)
    # `created` = when the SESSION ended, not when this write happens (#123).
    # Stamped here — not left to store's setdefault-now — so a heal/re-serialize
    # of an old transcript carries its true age and store's pointer guard can
    # keep it from stealing `latest` from a newer session. Needs a transcript
    # FILE; a hook host that provides none falls through to setdefault-now.
    if transcript_path is not None:
        checkpoint["created"] = _session_end_stamp(transcript_path)
    # Bind the checkpoint to its exact source content (#125). The sha is
    # computed by the caller at read time, before any LLM work; absent
    # (unreadable file / no file) means no stamp — readers tolerate that.
    if transcript_sha:
        checkpoint["transcript_hash"] = transcript_sha
    if config.carry_enabled():
        # Deterministic carry (#33 Phase 2): fold the previous checkpoint's
        # unresolved items in BEFORE the write rotates it away. Clock = this
        # checkpoint's own stamp (scar: never default to wall clock when a
        # stamp exists), wall time only as fallback for stampless paths.
        # Advisory feature — a raise here must never cost us the checkpoint
        # itself (a briefing missing carried items is strictly better than
        # no briefing at all; same idiom as the rejection-ledger swallow below).
        try:
            # fallback=False (#94): on a project's first serialize there is no
            # per-project pointer, and the global pointer is another project's
            # checkpoint — carrying from it would write foreign items into
            # this project's bucket permanently. No prev -> no carry.
            prev = store.read_latest(project, fallback=False)
            now = store._created_epoch(checkpoint.get("created")) or time.time()
            events = store.resolutions(project_dir=project)
            resolved = frozenset(ref for ref, evt in events.items()
                                 if store.is_resolved(evt))
            # #268: merge's observation sink — every prev/native pair that
            # clears the corroboration predicate, as (item_id, origin_session,
            # origin_author). merge only DECIDES; the ledger write is ours.
            observed: list = []
            checkpoint = carry.merge(checkpoint, prev, now,
                                     floor=config.carry_floor(),
                                     cap=config.carry_max(),
                                     resolved=resolved,
                                     observed=observed)
            # #14: text-target supersession links bound to prev-item ids ->
            # candidate events, gated so a human verdict is never overridden
            # (see _emit_supersede_candidates). Same fail-open try as merge
            # itself — a broken emission must never cost the checkpoint.
            # Stamp ids BEFORE binding: fresh natives are only stamped inside
            # write_checkpoint (after this block), so without this every new
            # item binds with new_id="" and the event carries no target.
            # Setdefault-idempotent — write_checkpoint's re-stamp no-ops.
            store._stamp_item_ids(checkpoint)
            pairs = carry.bind_links(checkpoint, prev)
            # ONE forgotten-keys read for both emitters (#268): each writes to
            # the same append-only log, each must refuse a tombstoned value,
            # and both must see the same ledger the `resolved` set above was
            # computed from. A raise here aborts BOTH emissions and keeps the
            # checkpoint — the fail-safe direction either emitter would take
            # on its own.
            forgotten = store.forgotten_content_keys(project_dir=project)
            _emit_supersede_candidates(pairs, events, project,
                                       forgotten=forgotten)
            # Corroboration rows land AFTER the candidates, deliberately: the
            # same capture can suggest a supersession and record an agreement
            # about the same item, and the corroboration reader measures its
            # count against the LATEST contradiction — so the candidate has to
            # be on the log first for that comparison to be honest.
            _emit_corroborations(
                observed, events,
                _forgotten_item_ids(forgotten, checkpoint, prev),
                project, str(checkpoint.get("session_id") or ""))
        except Exception:  # keep the unmerged checkpoint, proceed to write
            pass
    out = store.write_checkpoint(session_id, checkpoint, project_dir=project)
    if out is None:
        # #421: the write boundary refused (kill switch) — nothing landed, so
        # there is nothing to ledger rejections against either.
        return None
    # #480 slice 3: verify pending agent resolve candidates for THIS project
    # against THIS session's transcript. Independent of config.carry_enabled()
    # — an agent's claim can be confirmed even on a project's very first
    # serialize after the candidate landed. Its own try/except (not folded
    # into the carry block above): a broken verification pass must never cost
    # the checkpoint OR ride on carry being on.
    try:
        _verify_agent_resolutions(project, messages)
    except Exception:
        log.warning("agent resolution verification pass failed", exc_info=True)
    # #376: record what the checkers REJECTED, after write_checkpoint because
    # that is where item ids are guaranteed stamped (the merge branch above
    # only stamps when it runs). Its own append-only stream, never events.jsonl
    # — a rejection folded on item_ref would resolve the item and hide exactly
    # what it describes. Fail-open: an advisory counter never costs a capture.
    try:
        for row in serializer.verification_rejections(checkpoint):
            store.append_verification(row["item_ref"], row["check"],
                                      row["reason"], project_dir=project)
    except Exception:
        pass
    return out


def _emit_supersede_candidates(pairs, events: dict, project,
                               forgotten=None) -> int:
    """Turn `carry.bind_links` triples into `supersede-candidate:*` events,
    gated so a machine SUGGESTION never overrides a human verdict (#14,
    human-speaks-once).

    `events` is the SAME `store.resolutions` fold the serialize block already
    fetched for `resolved` — reused, not re-read, so this stays consistent
    with the resolved set computed moments earlier in the same call.

    Gate per (old_id, new_id, old_text) triple:
    (a) prior = events.get(old_id) — the latest lifecycle fact for old_id.
    (b) prior exists and its source isn't "serializer" -> a HUMAN spoke
        (confirmed via superseded-by, rejected via reopened, or anything
        else typed by a person) -> skip, forever. The gate itself is why a
        latest-event check is enough for permanence: machine events only
        ever land through this function, and this function refuses to
        write over a non-serializer prior, so once a human event is latest
        no future serialize run can dethrone it — there is no path back to
        a machine-authored latest.
    (c) prior exists, is a serializer-authored candidate, and already points
        at this same new_id -> idempotent, skip (re-running serialize on an
        unchanged pair must not spam the log).
    (d) otherwise -> append. Covers both the fresh case (no prior) and the
        candidate-changed case (prior candidate names a DIFFERENT new_id —
        the carry target moved, so a fresh candidate replaces it as latest).

    Forget gate (#419): `old_text` is a PREV item's raw text, and this runs
    BEFORE write_checkpoint's forget gate — so a forgotten value surviving in
    the prev checkpoint under a never-tombstoned id (sibling-id shape, #418)
    would land as plaintext `item_text` in append-only events.jsonl, forever.
    A pair whose old_text canonicalizes into the forgotten set (the same
    normalize.content_key keying store._drop_forgotten uses) is skipped
    entirely. Fail-safe direction: if the forgotten-keys read raises, emit
    NOTHING — a missed suggestion costs a candidate event; a leaked value
    costs the deletion guarantee.

    `forgotten` (#268): the caller may INJECT that key set, so the pipeline
    reads the ledger once and both emitters gate on the identical answer.
    None means read it here — the fail-safe read above, unchanged, which is
    also what a direct caller gets.

    Returns the number of events actually appended."""
    appended = 0
    if forgotten is None:
        try:
            forgotten = store.forgotten_content_keys(project_dir=project)
        except Exception:
            return 0  # can't prove a value isn't forgotten -> emit nothing
    for old_id, new_id, old_text in pairs:
        if not new_id:
            continue  # defense-in-depth: never write a candidate with no
                      # target ("supersede-candidate:") — the wiring stamps
                      # ids before binding, but a caller that skips that
                      # step must not corrupt the event log
        if forgotten and normalize.content_key(old_text or "") in forgotten:
            continue  # #419: tombstoned VALUE — its text must never reach
                      # the append-only audit trail
        prior = events.get(old_id)
        if prior and str(prior.get("source") or "") != "serializer":
            continue  # human spoke — machine stays silent forever
        if prior and prior.get("status") == f"supersede-candidate:{new_id}":
            continue  # idempotent — same candidate already latest
        if store.append_event(old_id, f"supersede-candidate:{new_id}",
                              source="serializer", item_text=old_text,
                              project_dir=project):
            appended += 1
    return appended


def _forgotten_item_ids(forgotten: set, *checkpoints) -> set:
    """The ids, across `checkpoints`, of items whose canonical VALUE has been
    tombstoned (#268 emission gate, #402 keying).

    The slice-2 triple carries an item id and an origin, no text — and the
    forget ledger is keyed on the VALUE, not on an id, precisely because one
    value can live under sibling ids (#418). Resolving one against the other
    needs the checkpoints the triples were derived from, which the caller
    already holds: the merged checkpoint (where the corroborated item ends up)
    and prev (where the value may still survive under a never-tombstoned id,
    the sibling shape #419 closed for the supersede stream).

    Ids, not texts, are what crosses into the emitter — so no forgotten value
    is ever carried near the append-only log, only the knowledge that an id
    must not be written about. Free when nothing was ever forgotten here."""
    out: set = set()
    if not forgotten:
        return out
    for cp in checkpoints:
        if not isinstance(cp, dict):
            continue  # no prev on a project's first serialize
        for item in serializer.iter_items(cp):
            if (item.get("id")
                    and normalize.content_key(item.get("text") or "") in forgotten):
                out.add(item["id"])
    return out


def _origin_on_disk(origin_session: str, project) -> bool:
    """G7: does the session that FIRST wrote this claim still exist here?

    Slice 2's predicate can only read what the item CLAIMS about its origin
    (#268 S1 binds it at write time, and the serialize boundary strips any
    model-emitted binding — but a hand-edited or synced checkpoint can still
    assert an origin nobody can produce). A witness that cannot be produced is
    not a witness, so the claim is checked against the per-session checkpoint
    files store.write_checkpoint lands, and scoped to THIS project: a
    checkpoint that exists but belongs elsewhere cannot vouch for a claim
    here.

    Absent, unreadable, torn, GC'd, or foreign all answer the same way — no.
    store.read_checkpoint is total (it swallows the bad-path/torn-file cases
    itself), so there is nothing here to catch."""
    cp = store.read_checkpoint(origin_session)
    if not isinstance(cp, dict):
        return False
    return cp.get("project_slug") == store.project_slug(project)


def _emit_corroborations(observed, events: dict, forgotten_ids: set, project,
                         observer: str) -> int:
    """Turn `carry.merge`'s observation triples into corroboration events
    (#268 slice 3) — the durable, auditable form of "somebody else's claim was
    independently restated here".

    The row is a POINTER and a WITNESS, nothing else:
      kind      "corroboration"
      item_ref  `store.corroboration_ref(item_id)` — NAMESPACED. Never the
                bare id: `resolutions` folds on item_ref alone and
                `is_resolved` resolves nearly everything (scar 0025), so a
                bare-ref row would hide the item it supports and would
                displace a human's superseded-by verdict as latest (#376).
      status    "corroborated-by:<observing session>"
      source    "serializer" — machine-authored, same as a supersede candidate.
    No `item_text`, ever: this log is append-only and never rewritten, so a
    value written here outlives every deletion the user can ask for (#419).

    Written through `store.append_event`, so redaction and the write-audit
    seam (#431) come free — this function opens no file of its own.

    Gates, each refusing in the same direction as the slice-2 predicate (a
    missed corroboration costs a boost; a forged one costs the axis):
      - unnameable witness -> nothing. The row's entire payload is WHO agreed.
      - idempotency: an (item, observing session) already on the record is
        skipped, and one call writes at most one row per item — every triple
        in a call shares the observer, so the rest are the same witness. Bound
        to `recorded` (every row ever written), never to `origins` (the rows
        that currently count): keying on the latter would let a demotion hand
        an existing witness a second vote.
      - forgotten: neither a value-tombstoned id (`forgotten_ids`, computed by
        _forgotten_item_ids) nor an id whose own latest lifecycle event is a
        tombstone may be written about at all.
      - G7 `_origin_on_disk`.
    A fold that cannot be read means duplicates cannot be ruled out, so the
    whole call writes nothing.

    `events` is the SAME `store.resolutions` fold the serialize block already
    fetched, reused exactly as _emit_supersede_candidates reuses it.

    Returns the number of events actually appended."""
    if not observed or not observer:
        return 0
    try:
        recorded = store.corroborations(project_dir=project)
    except Exception:
        return 0  # can't prove this isn't a re-run -> write nothing
    appended = 0
    written: set = set()
    for item_id, origin, _origin_author in observed:
        if not item_id or item_id in written or item_id in forgotten_ids:
            continue
        prior = events.get(item_id)
        if prior and str(prior.get("status") or "").lower().startswith(
                store._FORGOTTEN_PREFIX):
            continue  # tombstoned by id — nothing to corroborate, ever
        if observer in recorded.get(item_id, {}).get("recorded", ()):
            continue  # this witness already spoke about this item
        if not _origin_on_disk(origin, project):
            continue
        written.add(item_id)
        if store.append_event(store.corroboration_ref(item_id),
                              f"corroborated-by:{observer}",
                              kind="corroboration", source="serializer",
                              project_dir=project):
            appended += 1
    return appended


# ---- #480 slice 3: serialize-time verification of agent resolve candidates ----

# The confirming event's status. Deliberately does NOT start with
# "resolving-candidate" or "supersede-candidate" (store.is_resolved's and
# _tie_rank's exemption prefixes, scar 0025's shape) — a CONFIRMED claim must
# resolve and withhold like any ordinary human resolution (_tie_rank rank 1),
# never sit at rank 0 alongside the candidate it replaces. See
# tests/test_store.py's near-collision guard.
AGENT_VERIFIED_STATUS = "resolved-agent-verified"


def _pending_agent_candidates(events: dict) -> dict:
    """{item_ref: evidence quote} for every ref whose LATEST lifecycle event
    (store.resolutions' fold — already latest-by-ts, ties broken by content)
    is a still-pending agent resolve candidate (#480 slice 2). A ref
    superseded by ANY later event — a human resolve, a reopen, or this same
    pass's own prior confirming event — is not pending; the fold already
    resolves that, this only filters on the folded result. That is what
    makes both idempotence (a confirmed ref's latest event is no longer
    'resolving-candidate') and the human-reopen case (a later 'reopened'
    event is latest) come free, with no extra bookkeeping here."""
    out: dict = {}
    for ref, evt in events.items():
        if not isinstance(evt, dict):
            continue
        if str(evt.get("status") or "") != "resolving-candidate":
            continue
        if str(evt.get("source") or "") != "agent":
            continue
        evidence = evt.get("note")
        if isinstance(evidence, str) and evidence.strip():
            out[ref] = evidence
    return out


def _verify_agent_resolutions(project, messages) -> int:
    """#480 slice 3: byte-check every pending agent resolve candidate's
    evidence quote against THIS session's transcript — same matching stack
    verify_quotes already holds verbatim capture claims to (#125), reused,
    not duplicated: an agent's evidence meets the same bar a capture claim's
    quote meets.

    Quote found -> a confirming event, source="serializer",
    status=AGENT_VERIFIED_STATUS, note recording which role (if
    determinable) carried the quote. From here the item withholds like any
    ordinary resolution, credited as agent-verified. Quote not found ->
    nothing written; the candidate stands, exactly as live as before, for a
    human's resolve/reverify or a future serialize to settle.

    `events` is read fresh, scoped to `project` — store.resolutions reads
    that project's OWN events.jsonl (store._events_path keys on
    project_slug), so a candidate belonging to a different project can never
    be reached from here, let alone confirmed against this transcript (#480
    cross-project discipline: the store's per-project file layout does the
    isolating, this function never crosses it).

    Returns the number of confirming events appended. Callers wrap this in
    their own try/except (capture.run does) — a broken pass here must never
    fail the serialize itself."""
    events = store.resolutions(project_dir=project)
    pending = _pending_agent_candidates(events)
    if not pending:
        return 0
    haystack = serializer.stripped_transcript(messages) if messages else ""
    confirmed = 0
    for ref, evidence in pending.items():
        found, role = serializer.verify_agent_evidence(
            evidence, messages, haystack=haystack)
        if not found:
            continue
        if store.append_event(
                ref, AGENT_VERIFIED_STATUS,
                note=f"verified agent evidence (role: {role})",
                source="serializer", project_dir=project):
            confirmed += 1
    return confirmed


def _session_end_stamp(path) -> str:
    """When the session in `path` ended, in checkpoint `created` format (#123):
    the transcript's last message timestamp, falling back to the file mtime
    (markdown/plain transcripts carry no per-row stamps), then to now."""
    stamp = transcript.last_timestamp(path)
    if stamp:
        return stamp
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))
