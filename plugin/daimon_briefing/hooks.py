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

from . import briefing, config, harvest, llm, serializer, store, transcript

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
        if not messages or len(messages) < config.min_messages():
            return
        try:
            # serialize_strict, NOT the never-raise serialize(): a swallowed
            # LLM/schema failure would exit through the old "skip" branch and
            # never reach the ledger — only a too-short session is a true skip.
            checkpoint = serializer.serialize_strict(
                session_id, messages, chat=_chat, deadline=deadline
            )
        except serializer.TooShortError:
            log.info("daimon: no checkpoint produced for session %s (skip)", session_id)
            return
        root = config.resolve_project_root(config.project_dir())
        store.write_checkpoint(session_id, checkpoint, project_dir=root)
        log.info(
            "daimon: wrote checkpoint for session %s (took %ds)",
            session_id,
            int(time.monotonic() - start),
        )
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
        checkpoint = store.read_latest(project_dir=project)
        if checkpoint is None:
            return None
        # Withhold (#103 I1): this in-process injection path used to render the
        # RAW checkpoint, so a resolved item still auto-injected into every new
        # session's context — `daimon brief` already suppressed it, this hook
        # didn't. Same fail-open rule as _cmd_brief: any resolutions() failure
        # falls back to the unfiltered checkpoint, never blocks injection. No
        # withheld-count note here — this is context injection, not a human-
        # facing brief, so suppression stays clean (no note to render).
        try:
            events = store.resolutions(project_dir=project)
            checkpoint, _withheld, _candidates = briefing.withhold(checkpoint, events)
        except Exception:
            pass
        text = briefing.render(checkpoint)
        if not text:
            return None
        return {"context": text}
    except Exception:
        log.exception("daimon: pre_llm_call failed (no briefing injected)")
        return None
