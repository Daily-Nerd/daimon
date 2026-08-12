"""Values hand-copied across the Python/JS seam, with nothing keeping them in sync.

reader.py owns the item-id shape; render.js carries its own copy because a pure
render module cannot import Python. The copy has been correct since v0.3.0 purely
because nobody edited one side — that is luck, not a guarantee, and the comment
"mirror of reader ITEM_ID_RE" is a claim no test was checking.
"""
import re
from pathlib import Path

import daimon_ui
from daimon_ui import reader

_RENDER_JS = Path(daimon_ui.__file__).parent / "static" / "render.js"

def _js_literal(name):
    """The source text of a top-level JS regex literal, without its slashes."""
    src = _RENDER_JS.read_text(encoding="utf-8")
    m = re.search(r"^export const " + re.escape(name) + r" = /(.+?)/;", src, re.MULTILINE)
    assert m, f"{name} is no longer a top-level regex literal in render.js"
    return m.group(1)

def test_act_item_id_re_still_mirrors_reader_item_id_re():
    """Drift here is silent in both directions: a widened Python pattern makes the
    feed drop refs it should link, and a widened JS pattern makes it link refs the
    server would reject. Neither raises, so only this comparison catches it."""
    assert _js_literal("ACT_ITEM_ID_RE") == reader.ITEM_ID_RE.pattern

def test_the_js_mirror_stays_anchored():
    """reader.py applies its pattern with fullmatch; render.js applies its copy with
    .test(), which searches. Identical text is only equivalent while the pattern
    keeps both anchors — drop ^ or $ and the JS copy starts accepting substrings
    while this file's text comparison still passes."""
    pattern = _js_literal("ACT_ITEM_ID_RE")
    assert pattern.startswith("^"), pattern
    assert pattern.endswith("$"), pattern
