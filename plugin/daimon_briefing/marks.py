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
FORMAT_VERSION = 5

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


# The request furniture (#1402, format 4). A panel is a block: its frozen
# header, then its cards, then its notes, and the block ends at the next blank
# line. The headers are registered public strings: a change to one routes
# through the public-vocabulary process, never a local edit.
REQUEST_PANEL_HEADER = "Requests waiting on you (from other projects):"
VERDICT_PANEL_HEADER = "Decisions on requests you sent:"
OWED_PANEL_HEADER = "Requests you accepted and still owe:"
OVERDUE_PANEL_HEADER = "Requests you sent that nobody is moving:"
PANEL_HEADERS = {REQUEST_PANEL_HEADER: "request", VERDICT_PANEL_HEADER:
                 "verdict", OWED_PANEL_HEADER: "owed",
                 OVERDUE_PANEL_HEADER: "overdue"}

# What a panel indents: the lines behind a card line, the overflow and the cut
# count, and the owed panel's close rule.
PANEL_INDENT = "  "

# A request id, as every card line prints it before any text a peer wrote.
REQUEST_ID_PATTERN = r"q-[0-9a-f]{12}"

# The owed panel's closing line: who may close an accepted request.
OWED_CLOSE_LINE = ("Accepted and owed. Close one now, no human needed: "
                   "`daimon request done <id> --evidence …`")

# The standing rulings block's title.
RULINGS_HEADER = "Standing rulings (human-ratified — honor these):"

# The decision count line (#766): `here` waits on this project, `elsewhere`
# on others. The wording and scope are frozen.
DECISION_COUNT_PLURAL = \
    "{n} decisions waiting on you here{elsewhere} - daimon decide"
DECISION_COUNT_SINGULAR = \
    "1 decision waiting on you here{elsewhere} - daimon decide"
DECISION_ELSEWHERE = " ({n} elsewhere)"

# The withheld trailer: what the briefing left out, counted, one line each.
WITHHELD_RESOLVED = "resolved item(s) withheld"
WITHHELD_QUARANTINED = "quarantined item(s) withheld"
WITHHELD_TEAM = " ({n} a teammate's)"

# The anchor drift block (#1402, format 5), printed after the body: a blank
# line, the title, then one line per drifted item,
# `- [GONE] <text>  (<anchor>)` or `- [changed] <text>  (<anchor>)`. The
# lines open with an item mark but are NOT items: a parser reads them by
# their block. DRIFT_GONE is code that is gone or cannot be read, DRIFT_CHANGED
# code whose body changed. DRIFT_UNANCHORED stands where a hand-edited anchor
# names nothing.
DRIFT_TITLE = "CODE DRIFT \u2014 verify before trusting (anchored code changed):"
DRIFT_GONE = "GONE"
DRIFT_CHANGED = "changed"
DRIFT_UNANCHORED = "malformed anchor"


def warning(text: str) -> str:
    """`text` as a warning line: the warning mark in front, once."""
    return text if text.startswith(WARNING_MARK) else f"{WARNING_MARK} {text}"


def handoff_title(tier, ts: str) -> str:
    """The baton block's title line, without its closing colon. Only the human
    tier reads as a person's; anything else is shown as agent text."""
    by = HANDOFF_BY_HUMAN if tier == "human" else HANDOFF_BY_AGENT
    return f"{HANDOFF_LEAD}{by}, {ts})"
