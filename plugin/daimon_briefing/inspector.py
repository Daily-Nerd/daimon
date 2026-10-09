"""Read-side trust inspection for one project-scoped cognitive item (#502).

The inspector keeps evidence axes independent.  It never turns a changed
source into a failed quote verdict, never treats a missing local transcript as
an unsupported host, and never promotes legacy origin metadata into a bound
receipt.  Filesystem paths remain internal to the resolver and are never
returned to CLI or JSON consumers.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from . import (config, display, provenance, redact, schema, scoring,
               serializer, store, tool_context, transcript, view)


SCHEMA_VERSION = 1
_ITEM_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{6,40}(?:-[0-9]+)?$")
_SOURCE_MESSAGE_LIMIT = 3
_SOURCE_CHAR_LIMIT = 600
_WS_RE = re.compile(r"\s+")
_REDACTION_MARKER_RE = re.compile(r"\[redacted:[^\]\r\n]+\]")


def valid_item_id(value) -> bool:
    """The bounded exact-id contract exposed by ``daimon why``."""
    return isinstance(value, str) and _ITEM_ID_RE.fullmatch(value) is not None


def _legacy_source(item: dict) -> dict | None:
    session_id = item.get("origin_session")
    if not provenance.valid_session_id(session_id):
        return None
    source = view.source_ref(session_id)
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


def _preceding_tool_context(item: dict) -> dict:
    """Validate stored #1010 metadata before exposing it to an inspector."""
    raw = item.get("preceding_tool_context")
    if raw is None:
        return {"status": "not_recorded", "reason": "not_recorded"}
    if not isinstance(raw, dict):
        return {"status": "unavailable", "reason": "invalid_metadata"}
    if raw.get("policy") != tool_context.POLICY:
        return {"status": "unavailable", "reason": "unsupported_policy"}
    if raw.get("version") != tool_context.VERSION or raw.get("message_limit") != tool_context.MESSAGE_LIMIT:
        return {"status": "unavailable", "reason": "invalid_metadata"}
    status = raw.get("status")
    if status == "unavailable":
        reason = raw.get("reason")
        if not isinstance(reason, str) or reason not in tool_context._REASONS:
            return {"status": "unavailable", "reason": "invalid_metadata"}
        result = {"status": status, "version": raw["version"], "reason": reason,
                  "policy": raw["policy"], "message_limit": raw["message_limit"]}
    elif status == "observed":
        contexts = raw.get("contexts")
        if not isinstance(contexts, list):
            return {"status": "unavailable", "reason": "invalid_metadata"}
        receipt = item.get("quote_provenance")
        binding = receipt.get("binding") if isinstance(receipt, dict) else None
        bound_ids = (binding.get("message_ids")
                     if isinstance(binding, dict)
                     and binding.get("mode") == "message-ids" else None)
        if (not isinstance(bound_ids, list)
                or not all(isinstance(value, str) and value for value in bound_ids)):
            return {"status": "unavailable", "reason": "invalid_metadata"}
        safe_contexts = []
        context_sources = []
        for context in contexts:
            if not isinstance(context, dict):
                return {"status": "unavailable", "reason": "invalid_metadata"}
            source_id = context.get("source_message_id")
            result_ids = context.get("preceding_tool_result_ids")
            boundary = context.get("boundary")
            examined = context.get("messages_examined")
            if (not isinstance(source_id, str) or not source_id
                    or not isinstance(result_ids, list)
                    or not all(isinstance(value, str) and value for value in result_ids)
                    or len(result_ids) > tool_context.MESSAGE_LIMIT
                    or len(set(result_ids)) != len(result_ids)
                    or boundary not in ("host_user_input", "message_limit")
                    or isinstance(examined, bool)
                    or not isinstance(examined, int)
                    or not 0 <= examined <= tool_context.MESSAGE_LIMIT):
                return {"status": "unavailable", "reason": "invalid_metadata"}
            if source_id not in bound_ids or source_id in context_sources:
                return {"status": "unavailable", "reason": "invalid_metadata"}
            context_sources.append(source_id)
            safe_contexts.append({
                "source_message_id": source_id,
                "preceding_tool_result_ids": list(result_ids),
                "boundary": boundary,
                "messages_examined": examined,
            })
        if set(context_sources) != set(bound_ids):
            return {"status": "unavailable", "reason": "invalid_metadata"}
        result = {"status": status, "version": raw["version"], "policy": raw["policy"],
                  "message_limit": raw["message_limit"],
                  "contexts": safe_contexts}
    else:
        return {"status": "unavailable", "reason": "invalid_metadata"}
    source = raw.get("source")
    if source is not None:
        if not provenance.valid_source_ref(source):
            return {"status": "unavailable", "reason": "invalid_metadata"}
        result["source"] = source
    return result


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


_SOURCE_REFUSALS = ("closed", "forgotten-set", "quarantine-set",
                    "withheld-item")


def _withheld_source(reason: str) -> dict:
    """The `why --source` refusal (#1065). A transcript window is drawn from
    the RAW pre-forget transcript, and redact_text only catches secret
    SHAPES, never free text a person deliberately forgot or quarantined. The
    refusal is therefore set-based, not per-item: a tombstone is a hash of the
    whole forgotten item's TEXT, so no key can scan an unrelated window to
    clear it. It publishes the reason and no count: a count would disclose how
    many tombstones other tenants hold (#899). Over-withholding is the
    fail-safe direction."""
    assert reason in _SOURCE_REFUSALS
    return {"state": "withheld", "reason": reason}


def _source_refusal(snap, *, item_withheld: bool) -> str | None:
    """Why `--source` must be refused, or None. The sets are the view's own
    facts: the machine-wide forgotten set (every local project and what
    teammates published), this project's quarantines, an unreadable trust
    ledger, then the item itself."""
    if snap is not None:
        if snap.closed:
            return "closed"
        if snap.forgotten:
            return "forgotten-set"
        if snap.quarantined:
            return "quarantine-set"
    return "withheld-item" if item_withheld else None


_INDEX_ONLY_NOTE = ("this project's own checkpoint surfaces do not hold "
                    "this item; content is served from the recall index "
                    "and its exact bytes cannot be independently verified "
                    "on this machine")


def _lifecycle_event(latest) -> dict | None:
    """The judged latest lifecycle event, shaped for the result. A tombstone
    is the bare word `forgotten` (the view collapses its status), never the
    key it carries (scar 0119)."""
    if latest is None:
        return None
    return {"status": latest.status, "timestamp": latest.ts,
            "source": latest.source}


def _corroboration(item: dict, entry) -> dict:
    # #983 change 3: `why` must show the SAME effective count the briefing
    # badge does: an item whose first writer was a provisional never
    # corroborates, ledger row or not (store.corroboration_origins_for reads
    # `item["origin_session"]`, already loaded above).
    references = sorted(store.corroboration_origins_for(item, entry))
    return {"count": len(references), "references": references}


def _unknown_axes(lifecycle: str, support: str) -> dict:
    return {
        "capture": "unknown",
        "provenance": "legacy-unbound",
        "locator": "unsupported",
        "bytes": "unknown",
        "current_support": support,
        "verifier_comparison": "unknown",
        "lifecycle": lifecycle,
    }


def _no_item(item_id: str, kind: str, slug, text) -> dict:
    return {
        "item_id": item_id,
        "kind": kind,
        "text": text,
        "trust": None,
        "quote": text,
        "author": None,
        "project_slug": slug,
        "session_id": None,
        "origin_session": None,
        "occurrences": 0,
    }


def _withheld_result(verdict, lineage, slug, include_source: bool) -> dict:
    """The `why` payload for an item the reader may not see. It is built from
    the `Withheld` and the lineage only and never receives an item, so no
    value, quote, receipt, tool context or transcript window can reach it. The
    id, the kind and the lifecycle still print: a person has to see that a
    quarantine or a forget exists to act on it. The evidence axes that rest on
    the item's own receipt cannot be read without the item, so they report
    unknown rather than inventing a value."""
    marker = display.withheld_json(verdict)
    result = {
        "schema_version": SCHEMA_VERSION,
        "item": _no_item(verdict.item_id, verdict.kind, slug, marker),
        "preceding_tool_context": None,
        "axes": _unknown_axes(lineage.lifecycle, "withheld"),
        "corroboration": {"count": 0, "references": []},
        "ranking": None,
        "receipt": None,
        "source": None,
        "lifecycle_event": _lifecycle_event(lineage.latest),
    }
    if include_source:
        result["source_excerpt"] = _withheld_source(
            _source_refusal(lineage.snapshot, item_withheld=True)
            or "withheld-item")
    return result


def _events_only_result(item_id: str, lineage, slug, include_source: bool
                        ) -> dict:
    """The `why` payload for an id whose copies are gone but whose lifecycle
    the ledger still names (a resolved loop that aged out of every retained
    checkpoint). There is no item to describe, and `blame` answers the same
    id the same way."""
    result = {
        "schema_version": SCHEMA_VERSION,
        "item": _no_item(item_id, "unknown", slug, None),
        "preceding_tool_context": _preceding_tool_context({}),
        "axes": _unknown_axes(lineage.lifecycle, "not-checked"),
        "corroboration": _corroboration({}, lineage.corroboration),
        "ranking": None,
        "receipt": None,
        "source": None,
        "lifecycle_event": _lifecycle_event(lineage.latest),
    }
    if include_source:
        refusal = _source_refusal(lineage.snapshot, item_withheld=False)
        result["source_excerpt"] = (
            _withheld_source(refusal) if refusal else _bounded_source(
                {}, None, provenance.SourceResolution("unsupported"), None))
    return result


def inspect_item(project_dir, item_id: str, *, include_source: bool = False,
                 resolver=None, now: float | None = None) -> dict | None:
    """Inspect one exact item inside one explicit project scope.

    The view decides what may be said: `view.lineage` (one full snapshot)
    supplies the verdict, the retained appearances, the judged events and the
    lifecycle, and nothing else here reads a ledger or a checkpoint. A
    `Withheld` verdict answers with `_withheld_result`, an id with no copy but
    a lifecycle with `_events_only_result`, an id with neither with None.

    `now` is injected (#840) because the ranking block below decays with age:
    a payload whose number depends on an un-injected clock cannot be pinned by
    a test, and this one is published for consumers to recompute."""
    if now is None:
        now = time.time()
    lineage = view.lineage(project_dir, item_id)
    verdict = lineage.verdict
    slug = store.project_slug(project_dir)
    if isinstance(verdict, view.Withheld):
        return _withheld_result(verdict, lineage, slug, include_source)
    if not isinstance(verdict, view.Found):
        if not lineage.events:
            return None
        return _events_only_result(item_id, lineage, slug, include_source)

    item = verdict.item
    kind = verdict.field.kind
    meta = verdict.meta
    index_only = verdict.source == "index"
    receipt: Any = item.get("quote_provenance")
    bound = provenance.valid_quote_receipt(receipt)
    source: Any
    if bound:
        source = receipt["source"]
        provenance_axis = "bound"
    else:
        # An index row names the session that held a copy, which can be a
        # teammate's mirror or a carrier: not an origin, so nothing is inferred.
        source = None if index_only else _legacy_source(item)
        provenance_axis = ("legacy-inferred" if source is not None
                           else "legacy-unbound")

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

    result = {
        "schema_version": SCHEMA_VERSION,
        "item": {
            "item_id": item_id,
            "kind": kind,
            "text": item.get("text"),
            "trust": item.get("trust"),
            "quote": item.get("quote"),
            "author": meta.author if meta else None,
            "project_slug": slug,
            "session_id": meta.session_id if meta else None,
            "origin_session": None if index_only else item.get(
                "origin_session"),
            "occurrences": len(verdict.occurrences),
        },
        "preceding_tool_context": _preceding_tool_context(item),
        "axes": {
            "capture": capture,
            "provenance": provenance_axis,
            "locator": resolution.state,
            "bytes": _bytes_axis(receipt, resolution, messages),
            "current_support": _support_axis(
                item, receipt, resolution, messages),
            "verifier_comparison": verifier,
            "lifecycle": lineage.lifecycle,
        },
        "corroboration": _corroboration(item, lineage.corroboration),
        # #840 (request q-6cd17264d205): the ordering key, published with the
        # inputs that produced it. `why` is the surface that exists to answer
        # "why is this item where it is", and ranking was the one axis it
        # could not answer, so a consumer had to reimplement scoring.py and
        # drift from it. Recomputed here rather than stored: the weight decays,
        # so a value written at capture time is stale by the time it is read.
        #
        # The kind picks the rules, decay rate and whether overdue
        # escalation applies at all, so a fixed type would publish a number
        # no read path actually ranks on. On the #674 index-only path the
        # index row carries no importance or first_seen, and the block says so
        # in its own inputs (`importance_source: default`, `age_days: null`)
        # rather than presenting substituted values as recorded ones.
        "ranking": scoring.explain(
            item, schema.KIND_TO_TYPE.get(kind, "recent_decision"), now),
        "receipt": receipt if bound else None,
        "source": source,
        "lifecycle_event": _lifecycle_event(lineage.latest),
    }
    if index_only:
        reason = next((n.split(":", 1)[1] for n in verdict.notes
                       if n.startswith("index_only:")), "team-mirror")
        result["index_only"] = {"reason": reason, "note": _INDEX_ONLY_NOTE}
    if include_source:
        refusal = _source_refusal(lineage.snapshot, item_withheld=False)
        result["source_excerpt"] = (
            _withheld_source(refusal) if refusal
            else _bounded_source(item, receipt, resolution, messages))
    return result


def _ranking_line(ranking: dict) -> str:
    """One line for the same numbers the JSON publishes (#840). The human
    receipt must not be strictly less informative than its own machine
    payload, and the inputs ride along because a bare weight is exactly the
    ranking-you-must-trust-blind the request objected to.

    `rules` rather than `item_type`: it names the rules the computation
    actually applied, which differ whenever the kind maps to no known type."""
    inputs = ranking["inputs"]
    age = inputs["age_days"]
    age_text = "age unknown" if age is None else f"{age:.1f}d old"
    importance = (f"importance {inputs['importance']}"
                  if inputs["importance_source"] == "item"
                  else f"importance {inputs['importance']} (default, unscored)")
    return (f"Ranking: weight {ranking['effective_weight']:.3f} "
            f"as {ranking['rules']} ({importance}, {age_text}, "
            f"{inputs['trust'] or 'untagged'} ceiling "
            f"{inputs['trust_ceiling']:.2f})")


_SOURCE_REFUSAL_WORDS = {
    "closed": "the trust ledger cannot be read",
    "forgotten-set": "a forget tombstone exists on this machine and the "
                     "transcript predates any forgetting",
    "quarantine-set": "this project holds a quarantine and a transcript "
                      "window could carry its value",
    "withheld-item": "this item is withheld",
}


def _withheld_marker(text: dict) -> str:
    """The one-line marker for the `{"state": "withheld", ...}` object a
    result publishes in place of a value (never the value)."""
    reason = text.get("reason")
    return display.withheld_marker(view.Withheld(
        None, "unknown", reason if reason in ("quarantine", "closed")
        else "forgotten", text.get("quarantine_id"), ""))


def _short_lines(result: dict, item: dict, axes: dict) -> list[str]:
    """The rendering of a result that has no item to describe (withheld, or an
    id with only a lifecycle): the headline, the item line, the lifecycle, the
    cure when the trust ledger is unreadable, the source line and the source
    refusal. No evidence-axis map is consulted, so nothing here can raise on
    an axis value that only a visible item has."""
    text = item.get("text")
    held = text if isinstance(text, dict) else None
    marker = _withheld_marker(held) if held is not None else None
    lines = [
        f"Now: capture {axes['capture']}; item "
        f"{'withheld' if marker else 'not retained'}; "
        f"lifecycle {axes['lifecycle']}",
        f"Item: [{item['item_id']}] [{item['kind']}] "
        f"{marker or '(content unavailable)'}",
    ]
    if held is not None and held.get("quarantine_id"):
        lines[-1] += f" (daimon trust show {held['quarantine_id']})"
    lines.append(f"Lifecycle: {axes['lifecycle']}")
    if held is not None and held.get("reason") == "closed":
        lines.append("Cure: the trust ledger cannot be read; "
                     "run: daimon status")
    source = result.get("source")
    if isinstance(source, dict):
        lines.append(
            f"Source: {source.get('host', 'unknown')} session "
            f"{source.get('session_id', 'unknown')}")
    excerpt = result.get("source_excerpt")
    if isinstance(excerpt, dict) and excerpt.get("state") == "withheld":
        lines.append("Source excerpt: withheld, " + _SOURCE_REFUSAL_WORDS.get(
            str(excerpt.get("reason")), "this item is withheld"))
    elif isinstance(excerpt, dict):
        lines.append(f"Source excerpt: {excerpt.get('text') or '(unavailable)'}")
        if excerpt.get("note"):
            lines.append(f"Source note: {excerpt['note']}")
    return lines


def human_lines(result: dict) -> list[str]:
    """Compact human rendering; JSON intentionally has no derived summary."""
    item = result["item"]
    axes = result["axes"]
    if result.get("ranking") is None:
        return _short_lines(result, item, axes)
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
    item_line = (f"Item: [{item['item_id']}] [{item['kind']}] "
                 f"{item.get('text') or '(content unavailable)'}")
    lines = [
        f"Now: capture {axes['capture']}; {source_state}; {support}",
        item_line,
        f"Capture: {axes['capture']}",
        f"Provenance: {axes['provenance']}",
        f"Locator: {axes['locator']}",
        f"Bytes: {axes['bytes']}",
        f"Current support: {axes['current_support']}",
        f"Verifier: {axes['verifier_comparison']}",
        f"Lifecycle: {axes['lifecycle']}",
        _ranking_line(result["ranking"]),
    ]
    index_only = result.get("index_only")
    if isinstance(index_only, dict):
        lines.append(
            f"Index-only ({index_only['reason']}): {index_only['note']}")
    corroboration = result["corroboration"]
    refs = ", ".join(corroboration["references"])
    lines.append(
        f"Corroboration: {corroboration['count']}"
        + (f" ({refs})" if refs else ""))
    temporal = result.get("preceding_tool_context")
    if isinstance(temporal, dict):
        status = temporal.get("status")
        if status == "observed":
            lines.append(
                f"Preceding tool results: observed ({len(temporal.get('contexts', []))} source window(s)); "
                "order does not prove use")
        elif status == "not_recorded":
            lines.append("Preceding tool results: not recorded")
        else:
            lines.append(
                f"Preceding tool results: unavailable ({temporal.get('reason', 'unknown')})")
    source = result.get("source")
    if isinstance(source, dict):
        lines.append(
            f"Source: {source.get('host', 'unknown')} session "
            f"{source.get('session_id', 'unknown')}")
    excerpt = result.get("source_excerpt")
    if isinstance(excerpt, dict) and excerpt.get("state") == "withheld":
        lines.append("Source excerpt: withheld, " + _SOURCE_REFUSAL_WORDS.get(
            str(excerpt.get("reason")), "this item is withheld"))
    elif isinstance(excerpt, dict):
        lines.append(f"Source excerpt: {excerpt.get('text') or '(unavailable)'}")
        if excerpt.get("note"):
            lines.append(f"Source note: {excerpt['note']}")
    return lines
