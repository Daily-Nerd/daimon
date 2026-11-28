"""#1399 + #1431: the character and line policy of `display`.

Stored text is foreign data. `display.one_line` returns it as one physical line
made only of characters that display as stored; `display.printable` is the
same character rule for the few places that keep newlines. The tables below
are written out here on purpose: they are the spec, and the code is checked
against them over every code point.
"""
import pytest

from daimon_briefing import display, normalize

# (first, last) code points that never display: removed by both functions.
REMOVED = [
    (0x00, 0x08), (0x0B, 0x1F), (0x7F, 0x9F),          # C0 (not tab/LF), DEL, C1
    (0x2028, 0x2029),                                  # line / paragraph separator
    (0x00AD, 0x00AD), (0x034F, 0x034F),               # soft hyphen, CGJ
    (0x061C, 0x061C),                                  # ALM
    (0x180B, 0x180F),                                  # Mongolian variation selectors
    (0x200B, 0x200B),                                  # ZWSP
    (0x200E, 0x200F),                                  # LRM, RLM
    (0x202A, 0x202E),                                  # bidi embeddings, overrides
    (0x2060, 0x2064), (0x2066, 0x2069), (0x206A, 0x206F),
    (0xFE00, 0xFE0D),                                  # variation selectors 1-14
    (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB),
    (0xE0000, 0xE007F), (0xE0100, 0xE01EF),            # tag characters, VS supplement
]
# Legitimate text needs these: they stay.
KEPT = ["\u200c", "\u200d", "\ufe0e", "\ufe0f", "\ufffc", "\ufffd"]

LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def _removed(cp):
    return any(lo <= cp <= hi for lo, hi in REMOVED)


def _all_chars():
    return [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]


def test_one_line_keeps_exactly_the_characters_that_display():
    out = display.one_line("".join(_all_chars()))
    survivors = set(out) - {" "}
    expected = {chr(c) for c in range(0x110000)
                if not 0xD800 <= c <= 0xDFFF and not _removed(c)
                and not chr(c).isspace()}
    assert survivors == expected


def test_printable_removes_the_same_characters_and_keeps_tab_and_newline():
    out = display.printable("".join(_all_chars()))
    gone = {chr(c) for c in range(0x110000)
            if not 0xD800 <= c <= 0xDFFF and _removed(c)}
    assert not (set(out) & gone)
    assert "\t" in out and "\n" in out
    assert set(out) == ({chr(c) for c in range(0x110000)
                         if not 0xD800 <= c <= 0xDFFF} - gone)


@pytest.mark.parametrize("kept", KEPT)
def test_legitimate_format_characters_survive(kept):
    assert display.one_line(f"a{kept}b") == f"a{kept}b"
    assert display.printable(f"a{kept}b") == f"a{kept}b"


def test_an_override_does_not_reorder_what_is_shown():
    assert display.one_line("ad\u202emin") == "admin"
    assert display.printable("ad\u202emin") == "admin"


def test_emoji_and_persian_sequences_are_intact():
    zwj = "\U0001f468\u200d\U0001f4bb"
    persian = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
    heart = "\u2764\ufe0f"
    assert display.one_line(f"{zwj} {persian} {heart}") == f"{zwj} {persian} {heart}"


def test_tag_characters_hidden_text_disappears():
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "ignore previous")
    assert display.one_line("hello" + hidden) == "hello"


@pytest.mark.parametrize("brk", list(LINE_BREAKS))
def test_every_line_break_becomes_one_space(brk):
    assert display.one_line(f"a{brk}b") == "a b"
    assert display.one_line(f"a {brk} {brk}b") == "a b"


def test_one_line_agrees_with_splitlines():
    # what str.splitlines splits on is exactly what the policy treats as a break
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        s = f"a{chr(cp)}b"
        if len(s.splitlines()) > 1:
            assert display.one_line(s) == "a b", hex(cp)


def test_removal_after_the_break_so_a_carriage_return_never_joins_words():
    assert display.one_line("a\rb") == "a b"
    assert display.one_line("a \u200b b") == "a b"


def test_printable_keeps_its_old_behaviour_for_a_carriage_return():
    assert display.printable("a\tb\nc\x1bd\re") == "a\tb\ncde"


def test_line_tree_flattens_every_string_except_declared_multiline_keys():
    node = {"text": "a\nb", "because": "c\rd", "quote": "x\ny",
            "n": 3, "items": [{"text": "e\nf"}, "g\nh"]}
    out = display.line_tree(node, keep=("quote",))
    assert out is node
    assert node == {"text": "a b", "because": "c d", "quote": "x\ny",
                    "n": 3, "items": [{"text": "e f"}, "g h"]}


def test_the_hash_fold_strips_nothing_the_display_set_keeps():
    # the forget-key fold and the display set are separate on purpose in
    # this change; what the fold strips, the display must also remove,
    # except what text legitimately needs.
    keep = set(KEPT) | {"\u200c", "\u200d", "\ufe0e", "\ufe0f"}
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        if normalize._INVISIBLE_RE.fullmatch(ch) and ch not in keep:
            assert _removed(cp), hex(cp)


def test_the_forget_key_of_an_override_is_unchanged_by_the_display_policy():
    # #1399 changes what is SHOWN. The key a tombstone holds is a separate,
    # deliberate change (a re-key): pin today's behaviour so it cannot move
    # as a side effect.
    assert normalize.content_key("ad\u202emin") != normalize.content_key("admin")
    assert normalize.content_key("ad\u200bmin") == normalize.content_key("admin")
    assert normalize.content_key("ad\u202emin") == "44cfb7234f83cbfa"
    assert normalize.content_key("admin") == "8c6976e5b5410415"


def test_a_baton_with_format_characters_is_still_accepted_and_cleaned_at_render():
    from daimon_briefing import handoff
    assert handoff.validate("do x \u202efirst") == "do x \u202efirst"
    assert display.has_unprintable("a\x1bb") is True
    assert display.has_unprintable("a\u202eb") is False
