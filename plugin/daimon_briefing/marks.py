"""The briefing text format, defined once (#1132 PR 7b).

daimon owns this format and `api.parse_briefing` reads it back, so the marks
a renderer writes and the marks the parser looks for are the same constants:
the greeting that opens a briefing, the ruling mark, the item marks, the
warning mark and the phrase that heads the block of items to verify. Stdlib
only and nothing else here: both the renderers and the read-side API import
it, and neither may drift from the other.
"""

# The first line of every briefing, and what a parser takes as its header
# once the warnings above it are set aside.
GREETING = "While you were away — here's where we left off."

# A standing ruling line starts with this.
RULING_MARK = "§"

# An item line starts with one of these (`briefing._line` writes the first).
ITEM_MARKS = ("-", "*")

# A line that carries this anywhere is a warning, not a header, ruling or item.
WARNING_MARK = "⚠"

# The phrase that heads the block of items describing state that may have
# changed outside the session; a line holding it is a warning too.
VERIFY_PHRASE = "VERIFY BEFORE TRUSTING"

WARNING_MARKERS = (WARNING_MARK, VERIFY_PHRASE)


def warning(text: str) -> str:
    """`text` as a warning line: the warning mark in front, once."""
    return text if text.startswith(WARNING_MARK) else f"{WARNING_MARK} {text}"
