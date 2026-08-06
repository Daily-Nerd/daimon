"""Read-side trust inspection for one project-scoped cognitive item (#502).

The inspector keeps evidence axes independent.  It never turns a changed
source into a failed quote verdict, never treats a missing local transcript as
an unsupported host, and never promotes legacy origin metadata into a bound
receipt.  Filesystem paths remain internal to the resolver and are never
returned to CLI or JSON consumers.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config, provenance, redact, schema, serializer, store, transcript


SCHEMA_VERSION = 1
_ITEM_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{6,40}(?:-[0-9]+)?$")
_SOURCE_MESSAGE_LIMIT = 3
_SOURCE_CHAR_LIMIT = 600
_WS_RE = re.compile(r"\s+")
_REDACTION_MARKER_RE = re.compile(r"\[redacted:[^\]\r\n]+\]")
_KINDS = {(field.section, field.key): field.kind for field in schema.ITEM_FIELDS}


def valid_item_id(value) -> bool:
    """The bounded exact-id contract exposed by ``daimon why``."""
    return isinstance(value, str) and _ITEM_ID_RE.fullmatch(value) is not None


def _read_checkpoint(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _project_checkpoints(project_dir) -> list[dict]:
    """One authoritative payload per session, newest first.

    ``project_surfaces`` deliberately includes rotation pointers and flat
    per-session files.  A session may therefore appear several times.  Prefer
    its immutable flat session file when it survives GC, otherwise choose one
    pointer deterministically.  Path identity never leaves this function.
    """
    root = config.checkpoint_dir()
    chosen: dict[str, tuple[tuple[int, str], dict]] = {}
    for path in store.project_surfaces(project_dir):
        checkpoint = _read_checkpoint(path)
        if checkpoint is None:
            continue
        session_id = checkpoint.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            continue
        immutable = int(path.parent == root and path.name == f"{session_id}.json")
        rank = (immutable, str(path))
        current = chosen.get(session_id)
        if current is None or rank > current[0]:
            chosen[session_id] = (rank, checkpoint)

    def recency(checkpoint: dict) -> tuple[float, str]:
        return (store._created_epoch(checkpoint.get("created")) or 0.0,
                str(checkpoint.get("session_id") or ""))

    return sorted((entry[1] for entry in chosen.values()),
                  key=recency, reverse=True)


def _item_occurrences(project_dir, item_id: str) -> list[dict]:
    occurrences = []
    for checkpoint in _project_checkpoints(project_dir):
        for section, key in store._ITEM_LISTS:
            for item in ((checkpoint.get(section) or {}).get(key) or []):
                if isinstance(item, dict) and item.get("id") == item_id:
                    occurrences.append({
                        "checkpoint": checkpoint,
                        "item": item,
                        "kind": _KINDS.get((section, key), key),
                    })
    return occurrences


def _legacy_source(item: dict) -> dict | None:
    session_id = item.get("origin_session")
    if not provenance.valid_session_id(session_id):
        return None
    origin = store.read_checkpoint(session_id)
    if isinstance(origin, dict):
        source = origin.get("source_ref")
        if provenance.valid_source_ref(source):
            return source
    source = {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session_id,
        "locator": "managed",
    }
    author = item.get("origin_author")
    if isinstance(author, str) and author.strip():
        source["author"] = author.strip()
    return source


def _lifecycle(event) -> str:
    if not isinstance(event, dict):
        return "active"
    status = str(event.get("status") or "").lower()
    if status.startswith("forgotten:"):
        return "forgotten"
    if status.startswith("superseded-by:"):
        return "superseded"
    return "resolved" if store.is_resolved(event) else "active"


def _messages(path: Path) -> list[dict] | None:
    try:
        return transcript.from_file(path)
    except (OSError, UnicodeError, ValueError):
        return None


def _rendered_digest(messages: list[dict]) -> str:
    rendered = serializer._render_transcript(
        serializer.extraction_messages(messages))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _bytes_axis(receipt, resolution, messages) -> str:
    if not provenance.valid_quote_receipt(receipt):
        return "unknown"
    if resolution.state != "resolved" or resolution.path is None:
        return "unknown"
    digest = receipt["digest"]
    if digest["scope"] == "raw-file":
        current = transcript.file_sha256(resolution.path)
    elif messages is not None:
        current = _rendered_digest(messages)
    else:
        current = None
    if current is None:
        return "unknown"
    return "unchanged" if current == digest["value"] else "changed"


def _safe_texts_by_id(messages: list[dict]) -> dict[str, str]:
    raw = serializer.message_texts_by_id(messages)
    daimon_ids = serializer.daimon_output_ids(messages)
    return {
        message_id: "" if message_id in daimon_ids
        else serializer.strip_injected(text)
        for message_id, text in raw.items()
    }


def _support_axis(item, receipt, resolution, messages) -> str:
    quote = item.get("quote") if isinstance(item, dict) else None
    if (not isinstance(quote, str) or not quote.strip()
            or resolution.state != "resolved" or messages is None):
        return "not-checked"
    texts_by_id = _safe_texts_by_id(messages)
    message_ids = provenance.binding_message_ids(receipt)
    if message_ids and all(message_id in texts_by_id for message_id in message_ids):
        scoped = "\n\n".join(texts_by_id[message_id]
                              for message_id in message_ids)
        if serializer.quote_matches(quote, scoped):
            return "message-id-match"
    haystack = serializer.stripped_transcript(messages)
    if serializer.quote_matches(quote, haystack):
        return "transcript-scan-match"
    return "not-reproduced"


def _message_by_id(messages: list[dict]) -> dict[str, dict]:
    out = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            out[message_id] = message
    return out


def _cap_disclosed_source(value: str) -> tuple[str, bool]:
    """Cap display text without splitting a final-boundary redaction marker."""
    if len(value) <= _SOURCE_CHAR_LIMIT:
        return value, False

    cut = _SOURCE_CHAR_LIMIT - 1
    for match in _REDACTION_MARKER_RE.finditer(value):
        if match.start() < cut < match.end():
            marker = match.group(0)
            prefix_limit = _SOURCE_CHAR_LIMIT - len(marker) - 2
            prefix = value[:prefix_limit].rstrip()
            return f"{prefix}…{marker}…", True
    return value[:cut].rstrip() + "…", True


def _bounded_source(item, receipt, resolution, messages) -> dict:
    """Return a display-safe excerpt without persisting or exposing a path."""
    quote = item.get("quote") if isinstance(item, dict) else None
    message_ids = provenance.binding_message_ids(receipt)
    if (resolution.state == "resolved" and messages is not None and message_ids):
        by_id = _message_by_id(messages)
        if all(message_id in by_id for message_id in message_ids):
            parts = []
            for message_id in message_ids[:_SOURCE_MESSAGE_LIMIT]:
                message = by_id[message_id]
                role = str(message.get("role") or "unknown")
                content = str(message.get("content") or "")
                parts.append(f"{role}: {content}")
            raw = _WS_RE.sub(" ", " ".join(parts)).strip()
            # Redact BEFORE the character cap.  Cutting a credential-shaped
            # token in half first could stop the detector matching and expose
            # a real prefix at the boundary.  This remains exactly one pass at
            # the final display boundary; only the message-count bound is
            # applied earlier.
            safe, _counts = redact.redact_text(raw)
            safe, char_truncated = _cap_disclosed_source(safe)
            return {
                "kind": "message-window",
                "text": safe,
                "message_ids": message_ids[:_SOURCE_MESSAGE_LIMIT],
                "truncated": (len(message_ids) > _SOURCE_MESSAGE_LIMIT
                              or char_truncated),
            }
    if isinstance(quote, str) and quote.strip():
        # Stored quotes already crossed the capture redaction boundary.  Do not
        # redact twice: a second pass can mutate visible evidence markers.
        return {
            "kind": "stored-quote",
            "text": quote,
            "message_ids": [],
            "truncated": False,
            "note": "exact raw message span is unavailable; showing the stored quote",
        }
    return {
        "kind": "unavailable",
        "text": None,
        "message_ids": [],
        "truncated": False,
        "note": "no bounded source excerpt is available",
    }


def inspect_item(project_dir, item_id: str, *, include_source: bool = False,
                 resolver=None) -> dict | None:
    """Inspect one exact item inside one explicit project scope."""
    occurrences = _item_occurrences(project_dir, item_id)
    resolutions = store.resolutions(project_dir=project_dir)
    event = resolutions.get(item_id)
    if not occurrences and event is None:
        return None

    if occurrences:
        selected = occurrences[0]
        checkpoint = selected["checkpoint"]
        item = selected["item"]
        kind = selected["kind"]
    else:
        checkpoint = {}
        item = {}
        kind = "unknown"

    receipt = item.get("quote_provenance")
    bound = provenance.valid_quote_receipt(receipt)
    if bound:
        source = receipt["source"]
        provenance_axis = "bound"
    else:
        source = _legacy_source(item)
        provenance_axis = "legacy-inferred" if source is not None else "legacy-unbound"

    if resolver is None:
        resolver = provenance.SourceResolver(
            claude_projects=config.claude_projects_dir(),
            current_author=config.author())
    resolution = (resolver.resolve(source) if source is not None
                  else provenance.SourceResolution("unsupported"))
    messages = (_messages(resolution.path)
                if resolution.state == "resolved" and resolution.path is not None
                else None)

    capture = receipt["outcome"] if bound else "unknown"
    verifier = "unknown"
    if bound:
        current = (provenance.QUOTE_VERIFIER_ID,
                   provenance.QUOTE_VERIFIER_VERSION)
        recorded = (receipt["verifier"]["id"],
                    receipt["verifier"]["version"])
        verifier = "same-version" if recorded == current else "different-version"

    corroboration = store.corroborations(project_dir=project_dir).get(item_id, {})
    references = sorted(corroboration.get("origins") or ())
    result = {
        "schema_version": SCHEMA_VERSION,
        "item": {
            "item_id": item_id,
            "kind": kind,
            "text": item.get("text"),
            "trust": item.get("trust"),
            "quote": item.get("quote"),
            "author": checkpoint.get("author"),
            "project_slug": store.project_slug(project_dir),
            "session_id": checkpoint.get("session_id"),
            "origin_session": item.get("origin_session"),
            "occurrences": len(occurrences),
        },
        "axes": {
            "capture": capture,
            "provenance": provenance_axis,
            "locator": resolution.state,
            "bytes": _bytes_axis(receipt, resolution, messages),
            "current_support": _support_axis(
                item, receipt, resolution, messages),
            "verifier_comparison": verifier,
            "lifecycle": _lifecycle(event),
        },
        "corroboration": {
            "count": len(references),
            "references": references,
        },
        "receipt": receipt if bound else None,
        "source": source,
        "lifecycle_event": ({
            "status": event.get("status"),
            "timestamp": event.get("ts"),
            "source": event.get("source"),
        } if isinstance(event, dict) else None),
    }
    if include_source:
        result["source_excerpt"] = _bounded_source(
            item, receipt, resolution, messages)
    return result


def human_lines(result: dict) -> list[str]:
    """Compact human rendering; JSON intentionally has no derived summary."""
    item = result["item"]
    axes = result["axes"]
    support = {
        "message-id-match": "quote supported by its bound message",
        "transcript-scan-match": "quote supported by transcript scan",
        "not-reproduced": "quote not reproduced",
        "not-checked": "quote not checked",
    }[axes["current_support"]]
    source_state = ({
        "unchanged": "source unchanged",
        "changed": "source changed",
        "unknown": f"source bytes unknown ({axes['locator']})",
    }[axes["bytes"]])
    lines = [
        f"Now: capture {axes['capture']}; {source_state}; {support}",
        f"Item: [{item['item_id']}] [{item['kind']}] "
        f"{item.get('text') or '(content unavailable)'}",
        f"Capture: {axes['capture']}",
        f"Provenance: {axes['provenance']}",
        f"Locator: {axes['locator']}",
        f"Bytes: {axes['bytes']}",
        f"Current support: {axes['current_support']}",
        f"Verifier: {axes['verifier_comparison']}",
        f"Lifecycle: {axes['lifecycle']}",
    ]
    corroboration = result["corroboration"]
    refs = ", ".join(corroboration["references"])
    lines.append(
        f"Corroboration: {corroboration['count']}"
        + (f" ({refs})" if refs else ""))
    source = result.get("source")
    if isinstance(source, dict):
        lines.append(
            f"Source: {source.get('host', 'unknown')} session "
            f"{source.get('session_id', 'unknown')}")
    excerpt = result.get("source_excerpt")
    if isinstance(excerpt, dict):
        lines.append(f"Source excerpt: {excerpt.get('text') or '(unavailable)'}")
        if excerpt.get("note"):
            lines.append(f"Source note: {excerpt['note']}")
    return lines
