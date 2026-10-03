"""#1129: one set of helpers for foreign text shown in a title or a line.

Stdlib only. A record's text (an ask, a reason, a loop) is unbounded prose
written by someone else; every surface that shows it as a title bounds it
HERE, in its own lane, and never cuts a string it composed itself.
"""

from __future__ import annotations

import re

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
