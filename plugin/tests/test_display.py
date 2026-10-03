"""#1129 PR A: the shared display-text helpers."""

import random

import pytest

from daimon_briefing import display, requests


def test_one_line_collapses_every_whitespace_run():
    assert display.one_line("  a \n\n b\t\tc  ") == "a b c"


def test_one_line_is_null_safe():
    assert display.one_line(None) == ""
    assert display.one_line("") == ""


def test_shorten_returns_short_text_whole():
    assert display.shorten("a  b\nc", 20) == "a b c"


def test_shorten_default_cuts_at_a_word_and_never_exceeds_limit():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    out = display.shorten(text, 30)
    assert out.endswith("…")
    assert len(out) <= 30
    assert out[:-1] in text


def test_shorten_default_hard_cuts_when_boundary_is_in_first_half():
    out = display.shorten("ab " + "x" * 100, 40)
    assert out.endswith("…")
    assert len(out) <= 40
    assert len(out) > 20


def test_shorten_hard_is_limit_plus_one():
    out = display.shorten("word " * 50, 40, hard=True)
    assert out == ("word " * 50).strip()[:40].rstrip() + "…"
    assert len(out) <= 41


def test_shorten_sentence_returns_first_sentence_without_ellipsis():
    text = "Please ship the fix today. " + "More detail follows here. " * 20
    assert display.shorten(text, 60, sentence=True) == (
        "Please ship the fix today.")


def test_shorten_sentence_falls_back_to_the_word_cut():
    text = "no sentence end here " * 30
    out = display.shorten(text, 60, sentence=True)
    assert out == display.shorten(text, 60)
    assert out.endswith("…")


def test_shorten_sentence_matches_short_ask_byte_for_byte():
    rnd = random.Random(7)
    words = ["Alpha.", "beta", "Gamma?", "delta", "Eps!", "x" * 90, "zz"]
    for _ in range(300):
        text = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 60)))
        assert requests.short_ask(text) == display.shorten(
            text, requests.ASK_CHARS, sentence=True)


@pytest.mark.parametrize("seed", range(5))
def test_property_default_mode_bound_and_ellipsis_rule(seed):
    rnd = random.Random(seed)
    alphabet = "ab \n\t.xyz"
    for _ in range(400):
        text = "".join(rnd.choice(alphabet)
                       for _ in range(rnd.randint(0, 300)))
        limit = rnd.randint(2, 120)
        out = display.shorten(text, limit)
        flat = display.one_line(text)
        assert "\n" not in out
        if len(flat) <= limit:
            assert out == flat
        else:
            assert out.endswith("…")
            assert len(out) <= limit
            assert flat.startswith(out[:-1])
        hard = display.shorten(text, limit, hard=True)
        assert len(hard) <= limit + 1
