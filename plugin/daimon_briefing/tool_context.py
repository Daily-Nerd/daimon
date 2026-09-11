"""Code-derived temporal context for bound assistant quotes (#1010).

This module deliberately contains no transcript or checkpoint I/O.  It turns
validated parser metadata and validated quote bindings into a bounded audit
observation.  The result says where tool-result rows occurred in the recorded
transcript; it never says that a model read or used them.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast


POLICY = "preceding-tools-v1"
VERSION = 1
MESSAGE_LIMIT = 20

_REASONS = {
    "unsupported_adapter", "coverage_unknown", "coverage_incomplete",
    "source_binding_unavailable", "source_unresolved", "source_not_assistant",
    "boundary_unknown", "invalid_metadata", "unsupported_policy",
    "not_recorded",
}


def unavailable(reason: str, *, source=None) -> dict:
    """Build the persisted unavailable shape without a misleading empty list."""
    if reason not in _REASONS:
        reason = "coverage_unknown"
    result = {
        "version": VERSION,
        "policy": POLICY,
        "message_limit": MESSAGE_LIMIT,
        "status": "unavailable",
        "reason": reason,
    }
    if isinstance(source, dict):
        result["source"] = deepcopy(source)
    return result


def _valid_coverage(coverage) -> bool:
    return (
        isinstance(coverage, dict)
        and coverage.get("version") == VERSION
        and coverage.get("adapter") == "claude-code"
        and coverage.get("available") is True
        and isinstance(coverage.get("rows"), list)
    )


def _valid_source_ids(ids) -> bool:
    return (
        isinstance(ids, list)
        and all(isinstance(value, str) and value for value in ids)
        and len(ids) == len(set(ids))
    )


def _find_bound_sources(item: dict, surviving_ids: set[str]) -> tuple[list[str], str | None]:
    """Find source ids from the item's own verified receipts.

    The serializer calls this after quote verification, so the small amount of
    structural validation here is intentional defense in depth for malformed
    or legacy records.
    """
    sources: list[str] = []
    saw_candidate = False
    if not isinstance(item, dict) or item.get("trust") != "verbatim":
        return [], "source_binding_unavailable"
    receipt = item.get("quote_provenance")
    binding = receipt.get("binding") if isinstance(receipt, dict) else None
    if isinstance(binding, dict) and item.get("quote") and binding.get("mode") == "message-ids":
        saw_candidate = True
        ids = binding.get("message_ids")
        if not _valid_source_ids(ids):
            return [], "source_binding_unavailable"
        for value in cast(list[str], ids):
            if value not in surviving_ids:
                return [], "source_unresolved"
            if value not in sources:
                sources.append(value)
    if not saw_candidate:
        return [], "source_binding_unavailable"
    return sources, None


def derive(item: dict, messages: list[dict], coverage: dict | None,
           *, source=None) -> dict:
    """Derive one item-wide observation from the surviving quote bindings.

    ``item`` is handled independently so multiple quote sources retain their
    per-item relationship and never become a flattened checkpoint-wide union.
    """
    receipt = item.get("quote_provenance") if isinstance(item, dict) else None
    original_source = (receipt.get("source") if isinstance(receipt, dict)
                       else source)
    if not _valid_coverage(coverage):
        return unavailable(
            coverage.get("reason", "coverage_unknown")
            if isinstance(coverage, dict) else "coverage_unknown",
            source=original_source,
        )
    validated_coverage = cast(dict[str, Any], coverage)
    rows = cast(list[dict[str, Any]], validated_coverage["rows"])
    if len(rows) != len(messages):
        return unavailable("coverage_incomplete", source=original_source)
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("id"), str)
        or not row.get("id")
        or not isinstance(row.get("tool_result"), bool)
        or not isinstance(row.get("host_user_input"), bool)
        for row in rows
    ):
        return unavailable("coverage_incomplete", source=original_source)
    by_id = {cast(str, row["id"]) for row in rows}
    sources, reason = _find_bound_sources(item, by_id)
    if reason:
        return unavailable(reason, source=original_source)
    positions = {row["id"]: index for index, row in enumerate(rows)}
    sources.sort(key=positions.__getitem__)

    contexts = []
    for source_id in sources:
        index = next(i for i, row in enumerate(rows)
                     if row.get("id") == source_id)
        if rows[index].get("role") != "assistant" or rows[index].get("tool_result"):
            return unavailable("source_not_assistant", source=original_source)
        examined: list[dict[str, Any]] = []
        boundary = None
        cursor = index - 1
        while cursor >= 0 and len(examined) < MESSAGE_LIMIT:
            row = rows[cursor]
            if row.get("host_user_input") is True:
                boundary = "host_user_input"
                break
            examined.append(row)
            cursor -= 1
        if len(examined) == MESSAGE_LIMIT:
            # A boundary immediately beyond the cap wins without becoming a
            # 21st examined message.
            boundary = ("host_user_input" if cursor >= 0
                        and rows[cursor].get("host_user_input") is True
                        else "message_limit")
        elif boundary is None and cursor < 0:
            # A prefix with no known boundary may be a truncated capture.
            return unavailable("boundary_unknown", source=original_source)
        tool_ids = [row["id"] for row in reversed(examined)
                    if row.get("tool_result") is True]
        contexts.append({
            "source_message_id": source_id,
            "preceding_tool_result_ids": tool_ids,
            "boundary": boundary,
            "messages_examined": len(examined),
        })
    result = {
        "version": VERSION,
        "policy": POLICY,
        "message_limit": MESSAGE_LIMIT,
        "status": "observed",
        "contexts": contexts,
    }
    if isinstance(original_source, dict):
        result["source"] = deepcopy(original_source)
    return result
