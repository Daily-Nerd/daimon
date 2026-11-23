"""The briefing text format, defined once (#1132 PR 7b).

daimon owns this format and `api.parse_briefing` reads it back, so the marks
a renderer writes and the marks the parser looks for are the same constants:
the greeting that opens a briefing, the ruling mark, the item marks, the
warning mark and the phrase that heads the block of items to verify. Stdlib
only and nothing else here: both the renderers and the read-side API import
it, and neither may drift from the other.
"""

# The version of what `api.parse_briefing` extracts from a briefing, exported
# as `api.BRIEFING_FORMAT` and never printed into one. Bump it with ANY change
# to the constants below or to the parser's rules; `test_render_briefing`
# hashes the constants and fails on a change that did not bump it.
FORMAT_VERSION = 3

# The first line of every briefing, and what a parser takes as its header
# once the warnings above it are set aside.
GREETING = "While you were away, here's where we left off."

# Greetings an earlier daimon wrote. A briefing is injected into a session and
# so lands in the transcript on disk, where it outlives the format: the
# serializer must still recognize these to strip them (the echo guard), though
# nothing renders them any more.
LEGACY_GREETINGS = ("While you were away \u2014 here's where we left off.",)

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

# The baton block (#523) leads a briefing: a title line, then one line per
# line of the baton, each behind the line mark, then a blank line. The title
# says who wrote it. `left deliberately` is a person's (the human tier); an
# agent's baton, or one whose source no table knows, says so.
HANDOFF_LEAD = "HANDOFF ("
HANDOFF_BY_HUMAN = "left deliberately by previous session"
HANDOFF_BY_AGENT = "written by an agent, unverified"
HANDOFF_LINE_MARK = "\u2192"


def warning(text: str) -> str:
    """`text` as a warning line: the warning mark in front, once."""
    return text if text.startswith(WARNING_MARK) else f"{WARNING_MARK} {text}"


def handoff_title(tier, ts: str) -> str:
    """The baton block's title line, without its closing colon. Only the human
    tier reads as a person's; anything else is shown as agent text."""
    by = HANDOFF_BY_HUMAN if tier == "human" else HANDOFF_BY_AGENT
    return f"{HANDOFF_LEAD}{by}, {ts})"
