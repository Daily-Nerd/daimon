"""Transcript access. Two sources:

1. from_session(session_id) — reads hermes session history via SessionDB. The hermes
   import is guarded (hermes only available in-hermes); unavailable -> [] not raise.
2. from_file(path) — CLI/dogfood fallback. `.jsonl` parses as an agent session
   transcript; anything else as plain text/markdown. Includes a dedicated
   branch for Windsurf Cascade's native transcript (#70).

All messages normalize to OpenAI-format dicts: {"role": str, "content": str},
plus an optional "id" (#358) when the host row carries a stable per-message
identifier (Claude Code JSONL `uuid`). Hosts without one — Windsurf Cascade's
native rows ({type, status, payload}, field-confirmed #70), the Codex event
stream (payload is just {type, message}), hermes SessionDB, markdown/plain
text — keep the exact two-key shape, and downstream quote verification falls
back to whole-transcript scanning.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _load_session_db():
    """Return a SessionDB instance, or None if hermes is not importable.

    # VERIFIED website/docs/developer-guide/session-storage.md:
    #   from hermes_state import SessionDB
    #   db = SessionDB()  # defaults to ~/.hermes/state.db
    #   db.get_messages_as_conversation(session_id) -> [{"role","content"}, ...]
    """
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception:
        return None
    try:
        return SessionDB()
    except Exception:
        return None


def from_session(session_id: str) -> list[dict]:
    """Read a session transcript by id. Returns [] when hermes is unavailable/empty."""
    db = _load_session_db()
    if db is None:
        return []
    try:
        msgs = db.get_messages_as_conversation(session_id)
    except Exception:
        return []
    return msgs or []


def _text_of(content) -> str:
    """Flatten Claude Code message content to plain text.

    String content passes through; block arrays keep only `text` blocks —
    thinking, tool_use, and tool_result blocks are noise for the serializer.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _from_jsonl(text: str) -> list[dict]:
    """Parse a JSONL agent transcript into conversation messages.

    Claude Code exposes stable-enough user/assistant rows today. Codex also
    exposes a `transcript_path` to hooks, but its docs explicitly say the format
    is not stable, so this parser accepts a small set of role/content shapes and
    ignores everything else. A noise-only file returns [] — never the raw-blob
    fallback.
    """
    objects: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        objects.append(obj)

    # Current Codex rollouts emit each visible turn twice: once as an
    # `event_msg` and once as a nested `response_item`. Prefer the event stream
    # when present so platform-injected developer context and duplicate turns
    # never reach the serializer.
    codex_messages: list[dict] = []
    for obj in objects:
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        role = {"user_message": "user", "agent_message": "assistant"}.get(
            payload_type
        )
        content = payload.get("message")
        if role and isinstance(content, str) and content.strip():
            codex_messages.append({"role": role, "content": content.strip()})
    if codex_messages:
        return codex_messages

    # Windsurf Cascade's native transcript (#70): rows carry
    # {type, status, <payload-key>} and the payload key does NOT always equal
    # the type (grep_search_v2 keeps payload key grep_search) — so text
    # carriers are matched by type, never derived from it. The `status` key
    # (deliberately NOT a key count: a schema-widened row must still parse,
    # or the whole branch silently disables and the role-less fallback drops
    # every planner_response) is what distinguishes a genuine Cascade row
    # from other hosts' JSONL that happens to reuse a `user_input` key.
    # canceled lines are dropped; done AND error both serialize (a
    # failed-but-emitted response is still context).
    windsurf_messages: list[dict] = []
    for obj in objects:
        obj_type = obj.get("type")
        if obj_type not in ("user_input", "planner_response"):
            continue
        if "status" not in obj:
            continue
        if obj.get("status") == "canceled":
            continue
        role = "user" if obj_type == "user_input" else "assistant"
        payload = obj.get(obj_type)
        if not isinstance(payload, dict):
            continue
        text_key = "user_response" if obj_type == "user_input" else "response"
        text = payload.get(text_key)
        if isinstance(text, str) and text.strip():
            windsurf_messages.append({"role": role, "content": text.strip()})
    if windsurf_messages:
        return windsurf_messages

    messages: list[dict] = []
    for obj in objects:
        if obj.get("isSidechain") or obj.get("isMeta"):
            continue

        role = _role_of(obj)
        if role is None:
            continue
        content = _content_of(obj)
        if content:
            msg = {"role": role, "content": content}
            # #358: Claude Code rows carry a stable per-message `uuid` —
            # keep it as the message id so verbatim items can bind to the
            # exact transcript entry their quote came from. A discriminating
            # FIELD, not a row shape (deadend #20): rows without a usable
            # string uuid keep the exact two-key shape.
            mid = obj.get("uuid")
            if isinstance(mid, str) and mid.strip():
                msg["id"] = mid.strip()
            messages.append(msg)
    return messages


def _role_of(obj: dict) -> str | None:
    """Best-effort role extraction for known JSONL transcript shapes."""
    for key in ("role", "type"):
        val = str(obj.get(key) or "").lower()
        if val in ("user", "assistant"):
            return val

    typ = str(obj.get("type") or obj.get("event") or "").lower()
    if typ in ("user_input", "user_message", "prompt"):
        return "user"
    if typ in ("assistant_message", "assistant_response", "agent_message", "response"):
        return "assistant"

    msg = obj.get("message")
    if isinstance(msg, dict):
        val = str(msg.get("role") or "").lower()
        if val in ("user", "assistant"):
            return val
    return None


def _content_of(obj: dict) -> str:
    """Best-effort content extraction for known JSONL transcript shapes."""
    msg = obj.get("message")
    if isinstance(msg, dict):
        text = _text_of(msg.get("content"))
        if text:
            return text

    text = _text_of(obj.get("content"))
    if text:
        return text

    for key in ("text", "prompt", "response"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    user_input = obj.get("user_input")
    if isinstance(user_input, dict):
        for key in ("user_response", "prompt", "text", "content"):
            val = user_input.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _stamp_epoch(stamp) -> float | None:
    """Epoch seconds for a transcript row's `timestamp`, or None when absent or
    malformed. Accepts fractional seconds and a trailing Z (Claude Code rows emit
    e.g. `2026-07-01T10:05:30.500Z`), unlike store._created_epoch's strict
    checkpoint format — the two clocks carry different precisions on purpose."""
    if not isinstance(stamp, str):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def last_timestamp(path) -> str | None:
    """Session-end stamp for a `.jsonl` transcript: the max top-level `timestamp`
    across rows, normalized to the checkpoint `created` format (#123). The max —
    not the last row — so out-of-order rows can't report an early end. None for
    non-jsonl files, unreadable files, or stamp-free transcripts; callers fall
    back (cli uses the file mtime)."""
    p = Path(path)
    if p.suffix != ".jsonl":
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    best = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        epoch = _stamp_epoch(obj.get("timestamp"))
        if epoch is not None and (best is None or epoch > best):
            best = epoch
    if best is None:
        return None
    return datetime.fromtimestamp(best, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Matches "**user**:", "user:", "**Assistant**:" etc. at line start.
_ROLE_RE = re.compile(r"^\s*\**\s*(user|assistant|system|tool)\s*\**\s*:\s*", re.IGNORECASE)


def file_sha256(path) -> str | None:
    """SHA-256 hex over a transcript file's RAW bytes (#125), or None when the
    file is unreadable. Binds a checkpoint to its source content, not just the
    filename stem, so a transcript later truncated, rotated, or edited is
    detectable. Hashes bytes, not decoded text — no encoding normalization can
    silently change the digest."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def from_file(path) -> list[dict]:
    """Parse a transcript file into OpenAI-format messages.

    `.jsonl` -> agent session transcript (user/assistant turns only).
    Markdown with role markers (**user**: / assistant: / ...) -> one message per turn.
    Plain text with no markers -> a single user message carrying the whole blob.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")

    if p.suffix == ".jsonl":
        return _from_jsonl(text)

    messages: list[dict] = []
    current_role = None
    buf: list[str] = []

    def flush():
        if current_role is not None:
            content = "\n".join(buf).strip()
            if content:
                messages.append({"role": current_role, "content": content})

    for line in text.splitlines():
        m = _ROLE_RE.match(line)
        if m:
            flush()
            current_role = m.group(1).lower()
            buf = [line[m.end():]]
        else:
            buf.append(line)
    flush()

    if not messages:
        blob = text.strip()
        if blob:
            messages.append({"role": "user", "content": blob})
    return messages
