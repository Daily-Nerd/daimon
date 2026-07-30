"""The ONE capture pipeline (#432): serialize -> stamp -> carry+fold ->
bind_links -> supersede emission -> write -> rejection ledger.

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

import time
from pathlib import Path

from . import carry, config, normalize, serializer, store, transcript


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
            checkpoint = carry.merge(checkpoint, prev, now,
                                     floor=config.carry_floor(),
                                     cap=config.carry_max(),
                                     resolved=resolved)
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
            _emit_supersede_candidates(pairs, events, project)
        except Exception:  # keep the unmerged checkpoint, proceed to write
            pass
    out = store.write_checkpoint(session_id, checkpoint, project_dir=project)
    if out is None:
        # #421: the write boundary refused (kill switch) — nothing landed, so
        # there is nothing to ledger rejections against either.
        return None
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


def _emit_supersede_candidates(pairs, events: dict, project) -> int:
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

    Returns the number of events actually appended."""
    appended = 0
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
