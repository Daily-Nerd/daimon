"""#1399: the one list of characters that never display.

Stdlib only and import-free on purpose: `display` (rendering) and, in a later
change, `normalize` (the forget-key fold) both import it, and a leaf module is
the only place both can reach without a cycle (`display` pulls `marks` and
`surfaces`; `normalize` stays stdlib-only).

Stored text is foreign data. A character in this set is not shown, never
"escaped": a terminal acts on it (controls), or it reorders or hides what a
person reads against what the record holds (bidi controls, zero-width,
invisible and tag characters). Explicit ranges, never `unicodedata.category`,
so the set does not move with the Python version.

Kept on purpose, because legitimate text needs them: ZWJ (U+200D, emoji
sequences and Indic shaping), ZWNJ (U+200C, Persian and Indic), the emoji and
text presentation selectors (U+FE0E, U+FE0F), and the two visible placeholders
(U+FFFC, U+FFFD).
"""

import re

# C0 except tab and newline, DEL, C1, and the Unicode line and paragraph
# separators: what a terminal acts on (ESC starts every cursor move, CR
# rewrites a row, NEL and the single-byte CSI/OSC introducers are C1).
CONTROLS = "\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029"

# What displays differently from what is stored, or not at all.
FORMAT = (
    "\u00ad"                # soft hyphen
    "\u034f"                # combining grapheme joiner
    "\u061c"                # Arabic letter mark (bidi)
    "\u180b-\u180f"         # Mongolian free variation selectors, vowel separator
    "\u200b"                # zero width space
    "\u200e\u200f"          # LRM, RLM (bidi)
    "\u202a-\u202e"         # bidi embeddings and overrides
    "\u2060-\u2064"         # word joiner, invisible operators
    "\u2066-\u2069"         # bidi isolates
    "\u206a-\u206f"         # deprecated format characters
    "\ufe00-\ufe0d"         # variation selectors 1-14 (FE0E, FE0F stay)
    "\ufeff"                # zero width no-break space
    "\ufff9-\ufffb"         # interlinear annotation
    "\U000e0000-\U000e007f"  # tag characters (invisible to a person, read by a model)
    "\U000e0100-\U000e01ef"  # variation selectors supplement
)

# A character class `+`: linear, no backtracking (scar 0022).
UNSAFE_RE = re.compile("[" + CONTROLS + FORMAT + "]+")

# The controls alone: what a writer may refuse (a baton with ESC is not
# recorded). The format set is cleaned at render for every writer instead.
CONTROLS_RE = re.compile("[" + CONTROLS + "]")
