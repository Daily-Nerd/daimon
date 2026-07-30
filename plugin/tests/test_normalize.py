"""#403: the one shared, stdlib-only text-normalization function.

canonical_text folds a claim to a comparison-stable form so the forget-ledger
value key and any render-time comparison cannot diverge: a variant of the same
sentence (case, NFKC-compatibility form, inserted invisible characters, a
look-alike script character) must fold to the SAME canonical string.

Prior art that normalizes before hashing commonly misses whole invisible
blocks and does no confusables folding — these tests pin the full range set
and the confusables skeleton so "normalized" here really is sound.
"""

from daimon_briefing import normalize


def test_ascii_identity_is_stable_and_idempotent():
    s = "release pipeline awaiting manual approval step"
    assert normalize.canonical_text(s) == s
    # idempotent: the canonical form is a fixed point
    assert normalize.canonical_text(normalize.canonical_text(s)) == s


def test_casefold_folds_case():
    assert normalize.canonical_text("Hello WORLD") == normalize.canonical_text("hello world")


def test_nfkc_folds_fullwidth_and_ligatures():
    # fullwidth latin -> ascii; the fi ligature -> "fi"
    assert normalize.canonical_text("ＡＢＣ") == "abc"
    assert normalize.canonical_text("ﬁle") == "file"


def test_strips_zero_width_and_formatting_chars():
    base = normalize.canonical_text("secret value")
    for cp in (
        "​",  # ZERO WIDTH SPACE
        "‌",  # ZERO WIDTH NON-JOINER
        "‍",  # ZERO WIDTH JOINER
        "⁠",  # WORD JOINER
        "﻿",  # ZWNBSP / BOM
        "⁤",  # INVISIBLE PLUS
        "⁦",  # LEFT-TO-RIGHT ISOLATE
    ):
        injected = f"secret{cp} value"
        assert normalize.canonical_text(injected) == base, f"{cp!r} not stripped"


def test_strips_the_full_invisible_range_set():
    # every block issue #403 enumerates, one representative per range
    base = normalize.canonical_text("ab")
    for cp in (
        "­",       # SOFT HYPHEN
        "͏",       # COMBINING GRAPHEME JOINER
        "᠋",       # MONGOLIAN FREE VARIATION SELECTOR ONE
        "᠍",       # MONGOLIAN FREE VARIATION SELECTOR THREE
        "︀",       # VARIATION SELECTOR-1
        "️",       # VARIATION SELECTOR-16
        "\U000e0001",   # LANGUAGE TAG (tag block)
        "\U000e007f",   # CANCEL TAG (tag block end)
        "￼",       # OBJECT REPLACEMENT CHARACTER
        "�",       # REPLACEMENT CHARACTER
    ):
        assert normalize.canonical_text(f"a{cp}b") == base, f"{cp!r} not stripped"


def test_collapses_control_and_whitespace_runs():
    assert normalize.canonical_text("a\t\n  b\r\n\x00c") == "a b c"
    assert normalize.canonical_text("  padded  ") == "padded"


def test_confusables_skeleton_folds_cyrillic_and_greek():
    # Cyrillic а/е/о and Greek ο fold onto their Latin look-alikes
    assert normalize.canonical_text("аbc") == "abc"          # Cyrillic a
    assert normalize.canonical_text("mеm") == "mem"          # Cyrillic e
    assert normalize.canonical_text("fοo") == "foo"          # Greek omicron
    # a whole word spelled in Cyrillic look-alikes folds to the Latin word
    assert normalize.canonical_text("соре") == "cope"


def test_content_key_is_bounded_and_stable():
    k1 = normalize.content_key("Hello WORLD")
    k2 = normalize.content_key("hello world")
    assert k1 == k2                         # variants share one key
    assert 0 < len(k1) <= 64                # bounded key
    assert all(c in "0123456789abcdef" for c in k1)


def test_content_key_over_blocks_on_bounded_input():
    # the canonical text is length-bounded before hashing (fail-safe: a prefix
    # collision OVER-blocks, the safe direction for a deletion guarantee) — two
    # inputs sharing a very long common prefix key identically.
    prefix = "x" * 8000
    assert normalize.content_key(prefix + "aaa") == normalize.content_key(prefix + "bbb")


def test_large_no_separator_input_completes():
    # scar 0022: every capture-path regex gets a long-input completion test.
    # No timing assert — COMPLETION is the signal (a quadratic blowup would
    # hang here, which a fail-open except-clause cannot catch).
    blob = "A1b2C3d4" * 12500  # 100k chars, no whitespace/separator run
    out = normalize.canonical_text(blob)
    assert out  # returned, did not hang
    assert normalize.content_key(blob)  # key path completes too


def test_non_str_input_is_total():
    assert normalize.canonical_text(None) == ""
    assert normalize.canonical_text(12345) == "12345"
