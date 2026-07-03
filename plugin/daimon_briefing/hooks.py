"""Hermes hook callbacks. Both hooks are defensive: a broken hook must NEVER break
the user's session, so everything is wrapped — failures log and give up silently.

# VERIFIED website/docs/guides/build-a-hermes-plugin.md (hook callback signatures):
#   on_session_end(session_id, completed, interrupted, model, platform, **kwargs)
#   pre_llm_call(session_id, user_message, conversation_history, is_first_turn,
#                model, platform, **kwargs) -> {"context": str} | str | None
"""

import logging
import time

from . import briefing, config, harvest, llm, serializer, store, transcript

log = logging.getLogger("daimon_briefing")

# Module-level seam so tests can inject a fake LLM client.
_chat = llm.chat


def on_session_end(session_id, completed=None, interrupted=None, model=None, platform=None, **kwargs):
    """End-of-session: read transcript -> serialize -> validate -> store. Never raises.

    DAIMON_TIMEOUT is the TOTAL budget for the serialize LLM work: the deadline is
    computed here at hook start and forwarded so retries cannot stack past it.
    Everything — including config access — lives inside the try; nothing may escape.
    """
    try:
        if config.is_disabled():
            return
        start = time.monotonic()
        deadline = start + config.timeout_seconds()
        messages = transcript.from_session(session_id)
        if not messages or len(messages) < config.min_messages():
            return
        checkpoint = serializer.serialize(session_id, messages, chat=_chat, deadline=deadline)
        if checkpoint is None:
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
    except Exception:  # a broken hook must not break the session
        log.exception("daimon: on_session_end failed for session %s (giving up)", session_id)


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
        checkpoint = store.read_latest(
            project_dir=config.resolve_project_root(config.project_dir())
        )
        if checkpoint is None:
            return None
        text = briefing.render(checkpoint)
        if not text:
            return None
        return {"context": text}
    except Exception:
        log.exception("daimon: pre_llm_call failed (no briefing injected)")
        return None
