"""#1129: one set of helpers for foreign text shown in a title or a line.

Stdlib only. A record's text (an ask, a reason, a loop) is unbounded prose
written by someone else; every surface that shows it as a title bounds it
HERE, in its own lane, and never cuts a string it composed itself.
"""

from __future__ import annotations

import re

from . import marks

_SPACE_RE = re.compile(r"\s+")
_MIN_SENTENCE = 20
_SENTENCE_END_RE = re.compile(r"[.?!](?= [A-Z])")

# The cap on an evidence quote shown beside its item. One number with the
# agent-claim cap (`briefing._AGENT_CLAIM_EVIDENCE_CHARS`, pinned equal by a
# test): both are copy-pasted transcript spans shown as a skimmable hint.
QUOTE_CHARS = 120


def one_line(text) -> str:
    """`text` on one line: every whitespace run becomes one space, ends
    stripped. Null-safe (`None` is the empty string)."""
    return _SPACE_RE.sub(" ", str(text or "")).strip()


def shorten(text, limit: int, *, sentence: bool = False,
            hard: bool = False) -> str:
    """Bound `text` to `limit` characters on one line.

    The text is one-lined first and returned whole when it fits. Otherwise:

    - default: a cut at the last word boundary, taken only when it sits in
      the back half (else a hard cut), then `…`. The result INCLUDING the
      `…` never exceeds `limit`.
    - `sentence=True`: the first sentence when it fits and is not a stub,
      returned WITHOUT `…` (the one documented exception to "ends with `…`
      when cut"); else the default cut. This is the `requests.short_ask`
      shape, byte for byte.
    - `hard=True`: `text[:limit].rstrip() + "…"`, so `limit + 1` characters
      at most. The "never exceeds `limit`" promise belongs to the default
      mode only.
    """
    flat = one_line(text)
    if len(flat) <= limit:
        return flat
    if hard:
        return flat[:limit].rstrip() + "…"
    if sentence:
        first = _SENTENCE_END_RE.search(flat)
        if first and _MIN_SENTENCE <= first.end() <= limit:
            return flat[:first.end()]
    window = flat[:limit - 1]
    cut = window.rfind(" ") if flat[limit - 1] != " " else len(window)
    # A boundary in the first half would throw away most of the text (one
    # long token after a short prefix): hard cut instead.
    body = window[:cut] if cut >= limit // 2 else window
    return body.rstrip() + "…"


def quote_span(quote, text, limit: int = QUOTE_CHARS) -> str:
    """The quote as shown beside an item's text: "" when there is none or
    when the text already contains it (showing it twice is noise), else the
    quote on one line, cut to `limit` with `…`. Containment compares the
    one-lined forms; a word character at either edge of the quote must sit on
    a word edge in the text ("merge" is not contained in "merged"), while a
    punctuation edge needs no guard, so `(a, b)` is found inside `f(a, b)`.
    The caller passes the item's ORIGINAL text, never a budget-shortened
    copy. Display only: the stored quote is untouched."""
    flat = one_line(quote)
    if not flat:
        return ""
    head = r"(?<!\w)" if re.match(r"\w", flat[0]) else ""
    tail = r"(?!\w)" if re.match(r"\w", flat[-1]) else ""
    if re.search(head + re.escape(flat) + tail, one_line(text)):
        return ""
    return shorten(flat, limit)


# ---- #1132: how a withheld item is shown ----------------------------------

_WITHHELD_REASONS = ("forgotten", "quarantine", "closed")


def _check_reason(withheld) -> str:
    reason = withheld.reason
    if reason not in _WITHHELD_REASONS:
        raise ValueError(f"unknown withheld reason: {reason!r}")
    return reason


def withheld_marker(withheld) -> str:
    """The one-line marker for a `view.Withheld`: the reason, and for a
    quarantine its record id. Never the value, its key or the item id."""
    reason = _check_reason(withheld)
    if reason == "quarantine":
        suffix = f" {withheld.quarantine_id}" if withheld.quarantine_id else ""
        return f"[withheld: quarantine{suffix}]"
    if reason == "closed":
        return "[withheld: trust ledger unreadable]"
    return "[withheld: forgotten]"


def withheld_json(withheld) -> dict:
    """The JSON form: `{"state": "withheld"}` (the encoding `why` has always
    used) plus the reason, and a quarantine's record id."""
    out = {"state": "withheld", "reason": _check_reason(withheld)}
    if out["reason"] == "quarantine" and withheld.quarantine_id:
        out["quarantine_id"] = withheld.quarantine_id
    return out


# ---- #1132 PR 9a: the one recall note ---------------------------------------

_RECALL_NOTES = {
    "stale": "the recall index could not be refreshed, so these results come "
             "from the last build and may be out of date",
    "closed": "part of this history is hidden because a trust ledger cannot "
              "be read",
}


def recall_note(notes) -> str | None:
    """One warning line for the notes a recall carries, or None. The CLI, the
    MCP tool and the viewer all show recall's notes through this, so no
    surface words them differently. A note is a code; the line names no
    project, no id and no count, so it cannot say how much is missing."""
    known = [_RECALL_NOTES[n] for n in ("stale", "closed") if n in notes]
    if not known:
        return None
    return marks.warning("recall: " + "; ".join(known))
