"""Host hook callbacks for in-process capture. Both hooks are defensive: a
broken hook must NEVER break the user's session, so everything is wrapped —
failures log, leave a ledger entry in serialize.log, and give up.

# VERIFIED host plugin guide (hook callback signatures):
#   on_session_end(session_id, completed, interrupted, model, platform, **kwargs)
#   pre_llm_call(session_id, user_message, conversation_history, is_first_turn,
#                model, platform, **kwargs) -> {"context": str} | str | None
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import (amendments, briefing, capture, config, harvest, ledger, llm,
               recall, serializer, store, transcript)

log = logging.getLogger("daimon_briefing")

# Module-level seam so tests can inject a fake LLM client.
_chat = llm.chat


def _ledger_failure(session_id, exc, elapsed, transcript_path=None):
    """Append a failed capture to serialize.log — the ledger `daimon status`
    and `daimon heal` parse. log.exception alone reaches nothing the CLI reads,
    so a failed in-process capture used to be silent, uncounted, non-healable.

    Two lines, byte-shaped like the spawn + result pair every spawned-CLI
    capture leaves (cli._SPAWN_RE / cli._RESULT_ERR_RE round-trip), so the
    per-session ledger attributes the failure and heal classifies it under its
    NORMAL rules: transcript file on disk -> healable (one retry ever, #26);
    none -> counted but not auto-repairable. The parser derives the session id
    from the transcript token's stem, so a host-provided path is used only when
    its stem IS the session id — otherwise the spawn and error lines would
    split across two ledger entries. Best-effort: must never raise into the
    hook's own never-raise contract."""
    try:
        path = str(transcript_path or "").strip()
        if not path or Path(path).stem != session_id:
            path = session_id
        try:
            project = config.resolve_project_root(config.project_dir())
        except Exception:
            project = None
        reason = " ".join(str(exc).split()) or type(exc).__name__
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(
                f"{stamp} session-end: spawned serialize for {session_id} "
                f"(reason: in-process capture, project: {project or '?'}) "
                f"(transcript: {path})\n"
            )
            f.write(f"error: {reason} (transcript: {path}) after {max(0, int(elapsed))}s\n")
    except Exception:
        log.exception("daimon: could not ledger capture failure for session %s", session_id)


def on_session_end(session_id, completed=None, interrupted=None, model=None, platform=None, **kwargs):
    """End-of-session: read transcript -> serialize -> validate -> store. Never raises.

    DAIMON_TIMEOUT is the TOTAL budget for the serialize LLM work: the deadline is
    computed here at hook start and forwarded so retries cannot stack past it.
    Everything — including config access — lives inside the try; nothing may
    escape, and any failure lands in serialize.log so status/heal see it (#142).
    """
    start = time.monotonic()
    try:
        if config.is_disabled():
            return
        deadline = start + config.timeout_seconds()
        # Identical-bytes guard (#185), same helper cli._run_serialize uses: when
        # the host hands us a real transcript file (optional — this host reads
        # messages via transcript.from_session, not a file, so a path is not
        # always available) and its bytes are unchanged since the checkpoint
        # already on disk for this session, skip rather than burn an LLM call
        # reproducing a byte-identical checkpoint. No transcript_path -> can't
        # hash -> proceeds exactly as before #185 (fail-open).
        transcript_path = kwargs.get("transcript_path")
        transcript_sha = None
        if transcript_path:
            transcript_sha = transcript.file_sha256(transcript_path)
            if store.transcript_unchanged(session_id, transcript_sha):
                log.info(
                    "daimon: skipped serialize for session %s: transcript "
                    "unchanged since checkpoint (hash match)",
                    session_id,
                )
                return
        messages = transcript.from_session(session_id)
        if not messages:
            # scar 0045: 0 parsed messages is the host-format-drift signature,
            # never a short session — the official skip line would legitimize
            # the loss as a policy outcome. Bare return, no skip record.
            return
        # #750: the SAME non-tool count serialize_strict gates on — raw len()
        # here let a tool-heavy transcript pass this door only to be refused
        # inside the pipeline. And a genuine skip is RECORDED, the way the CLI
        # door records its (cli.__init__ TooShortError branch): a bare return
        # left the session invisible to `daimon stats`. n == 0 stays scar-0045
        # territory (rows parsed, zero conversation) — no skip line either.
        n = serializer.conversation_message_count(messages)
        if n < config.min_messages():
            if n >= 1:
                ledger._append_serialize_log(
                    f"skipped serialize for {session_id}: transcript too short "
                    f"({n} < {config.min_messages()} messages)")
            return
        root = config.resolve_project_root(config.project_dir())
        try:
            # THE shared pipeline (#432): serialize -> stamps -> carry+fold ->
            # bind_links -> supersede emission -> write -> rejection ledger —
            # the SAME function cli._run_serialize calls, so a checkpoint's
            # contents no longer depend on which door the session left through
            # (tests/test_capture_parity.py guards this). Inside it,
            # serialize_strict — NOT the never-raise serialize(): a swallowed
            # LLM/schema failure would exit through the old "skip" branch and
            # never reach the ledger — only a too-short session is a true skip.
            # No `escalate` here ever (#360): escalation is heal-only, so its
            # cost scales with failure, not usage. transcript_path/sha may be
            # None — this host reads messages via transcript.from_session, not
            # always a file — in which case the created stamp falls back to
            # the store's setdefault-now, exactly as before.
            out = capture.run(
                session_id, messages, project=root, chat=_chat,
                deadline=deadline, transcript_path=transcript_path,
                transcript_sha=transcript_sha, capture_host=platform,
            )
        except serializer.TooShortError:
            # Unreachable while both doors share conversation_message_count
            # (#750); kept defensive. No ledger append here — a drift could
            # carry n == 0, and scar 0045 forbids recording that as a skip.
            log.info("daimon: no checkpoint produced for session %s (skip)", session_id)
            return
        if out is None:
            # #421: the write boundary refused (kill switch flipped since the
            # hook-start check). Nothing landed — a skip, never a lying success.
            log.info(
                "daimon: skipped checkpoint write for session %s: daimon "
                "disabled (DAIMON_DISABLE)",
                session_id,
            )
            return
        log.info(
            "daimon: wrote checkpoint for session %s (took %ds)",
            session_id,
            int(time.monotonic() - start),
        )
        # #246: the write staled the recall index. This hook runs after the
        # session ends — the one place a full rebuild costs nobody anything —
        # so the NEXT session's first-prompt recall-inject finds it fresh
        # instead of paying the rebuild on the user's critical path. warm()
        # never raises (and the outer except would ledger a lie: the
        # checkpoint IS written by this point).
        recall.warm()
        if config.scar_harvest_enabled():
            try:
                harvest.run(messages, project_root=root, session_id=session_id)
            except Exception:
                log.exception("daimon: scar harvest failed (checkpoint unaffected)")
    except Exception as exc:  # a broken hook must not break the session
        log.exception("daimon: on_session_end failed for session %s (giving up)", session_id)
        _ledger_failure(session_id, exc, time.monotonic() - start,
                        kwargs.get("transcript_path"))


def pre_llm_call(session_id=None, user_message=None, conversation_history=None,
                 is_first_turn=False, model=None, platform=None, **kwargs):
    """First turn of a new session: inject the 'while you were away' briefing.

    Returns {"context": briefing} to append to the user message, or None. Never raises.
    """
    try:
        if config.is_disabled():
            return None
        if not is_first_turn:
            return None
        project = config.resolve_project_root(config.project_dir())
        # #784: read_latest's global pointer is the most recent checkpoint of ANY
        # project, so a project with no bucket of its own was injected with a
        # foreign briefing on its first session. `daimon brief` already suppresses
        # that body by default (#96); this path did not, and it is the path with no
        # human reader. Same gate, same env var, so the two surfaces state the same
        # world. Asking read_latest not to fall back (rather than detecting after
        # the fact that it did) also covers the torn own-pointer, which falls
        # through to the global pointer while the project's path still exists.
        # The fallback stays ON when the project is UNKNOWN: there is no per-project
        # pointer to prefer, the global one is the only briefing that exists, and
        # nothing is foreign to a session with no project identity (pre-routing
        # hosts that do not pass a cwd). The leak needs a KNOWN project.
        allow_foreign = project is None or config.brief_global_fallback()
        checkpoint = store.read_latest(project_dir=project, fallback=allow_foreign)
        if checkpoint is None:
            # #693: standing rulings exist before the first checkpoint does —
            # a day-one ratification must reach the very next session.
            rulings = briefing.ruling_lines(project)
            return {"context": "\n".join(rulings)} if rulings else None
        # Withhold (#103 I1): this in-process injection path used to render the
        # RAW checkpoint, so a resolved item still auto-injected into every new
        # session's context — `daimon brief` already suppressed it, this hook
        # didn't. Same fail-open rule as _cmd_brief: any resolutions() failure
        # falls back to the unfiltered checkpoint, never blocks injection. No
        # withheld-count note here — this is context injection, not a human-
        # facing brief, so suppression stays clean (no note to render).
        try:
            events = store.resolutions(project_dir=project)
            # #691: same amendment annotations as `daimon brief` — the
            # injected context and the human brief must state the same world.
            checkpoint, _withheld, _candidates = briefing.withhold(
                checkpoint, events,
                amendments=amendments.renderable(project_dir=project))
            # #268: the witness count is a reason to weight a claim, so the
            # injected context states it exactly as the human brief does.
            # Rides the same fail-open try — the badge is advisory, and no
            # annotation is worth losing the injection over.
            checkpoint = briefing.mark_corroborated(
                checkpoint, store.corroborations(project_dir=project))
        except Exception:
            pass
        # #693: rulings are scoped to the RESOLVED project (never the raw
        # process cwd). Note read_latest above keeps its global fallback, so
        # the checkpoint may be another project's — the rulings are still
        # this project's own.
        text = briefing.render(checkpoint, project_dir=project)
        if not text:
            return None
        return {"context": text}
    except Exception:
        log.exception("daimon: pre_llm_call failed (no briefing injected)")
        return None
