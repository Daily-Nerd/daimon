"""The one place ledger text is split and rewritten (#1138).

`str.splitlines()` also breaks on U+2028, U+2029, U+0085 (and \\x0b, \\x0c,
\\x1c-\\x1e), and the ledgers write those raw (`ensure_ascii=False`). A
rewriter that splits with it tears the row, then drops the fragments as
"unparseable". These tests pin the replacement.
"""

import json
import random

import pytest

from daimon_briefing import jsonl

SEPS = [" ", " ", "\u0085"]


def _dump(row):
    return json.dumps(row, ensure_ascii=False)


@pytest.mark.parametrize("sep", SEPS)
def test_split_rows_keeps_a_row_with_a_unicode_separator_whole(sep):
    line = _dump({"id": "a", "note": f"before{sep}after"})
    assert sep in line
    assert jsonl.split_rows(line + "\n" + _dump({"id": "b"}) + "\n") == [
        line, _dump({"id": "b"})]


def test_split_rows_drops_blank_and_whitespace_only_lines():
    text = "\n  \n" + _dump({"a": 1}) + "\n\t\n\n" + _dump({"b": 2})
    assert jsonl.split_rows(text) == [_dump({"a": 1}), _dump({"b": 2})]


def test_split_rows_does_not_split_on_other_splitlines_separators():
    line = _dump({"note": "x\x0by\x1cz"})
    assert "\\u000b" in line  # json escapes them, so the line is one row
    assert jsonl.split_rows(line) == [line]


def test_split_rows_rejoins_a_row_torn_by_a_past_scrub_with_u2028():
    whole = _dump({"id": "a", "note": "left right"})
    torn = whole.replace(" ", "\n")  # what the old scrub wrote
    assert jsonl.split_rows(torn + "\n") == [whole]


def test_split_rows_rejoins_three_fragments_greedily():
    # The rejoin character is U+2028 whatever the lost one was (human-approved).
    whole = _dump({"id": "a", "note": "one two three"})
    torn = whole.replace(" ", "\n")
    assert torn.count("\n") == 2
    assert jsonl.split_rows(torn) == [whole]


def test_split_rows_rejoins_a_fragment_that_was_whitespace_only():
    whole = _dump({"id": "a", "note": "x   y"})
    torn = whole.replace(" ", "\n")
    assert jsonl.split_rows(torn + "\n" + _dump({"id": "b"})) == [
        whole, _dump({"id": "b"})]


def test_torn_tail_then_healed_append_is_not_mistaken_for_a_split_row():
    # A crash left `{"id":"a","note":"lef` with no newline; the next appender
    # heals with "\n" and writes a whole row. The boundary is NOT inside a
    # string that the next line closes, so nothing may be joined.
    torn = '{"id": "a", "note": "lef'
    good = _dump({"id": "b", "note": "ok"})
    assert jsonl.split_rows(torn + "\n" + good + "\n") == [torn, good]


def test_split_rows_leaves_garbage_lines_as_they_are():
    good = _dump({"id": "a"})
    assert jsonl.split_rows("garbage\n" + good + "\n}{\n") == [
        "garbage", good, "}{"]


def test_split_rows_lookahead_is_bounded_on_a_wall_of_torn_rows():
    # Quadratic-blowup guard: many unparseable openers must stay linear-ish.
    text = "\n".join('{"a": "x' for _ in range(5000))
    assert len(jsonl.split_rows(text)) == 5000


def test_property_random_rows_round_trip_byte_identical(tmp_path):
    rng = random.Random(1138)
    alphabet = ["a", "b", " ", '"', "\\", "\n", "\x0b", "é", "\U0001f600",
                *SEPS]
    for _ in range(200):
        rows = [{"id": f"r-{i}", "note": "".join(
            rng.choice(alphabet) for _ in range(rng.randint(0, 12)))}
            for i in range(rng.randint(1, 6))]
        text = "".join(_dump(r) + "\n" for r in rows)
        assert jsonl.split_rows(text) == [_dump(r) for r in rows]
        path = tmp_path / "ledger.jsonl"
        path.write_text(text, encoding="utf-8")
        before = path.read_bytes()
        assert jsonl.rewrite(path, lambda line, row: line) == 0
        assert path.read_bytes() == before


@pytest.mark.parametrize("sep", SEPS)
def test_rewrite_drops_only_the_asked_row_and_keeps_the_rest_byte_for_byte(
        tmp_path, sep):
    keep = _dump({"id": "keep", "note": f"a{sep}b"})
    drop = _dump({"id": "drop"})
    path = tmp_path / "ledger.jsonl"
    path.write_text(keep + "\n" + drop + "\n", encoding="utf-8")
    n = jsonl.rewrite(
        path, lambda line, row: None if row.get("id") == "drop" else line)
    assert n == 1
    assert path.read_bytes() == (keep + "\n").encode("utf-8")


def test_rewrite_keeps_an_unparseable_line_verbatim(tmp_path):
    good, drop = _dump({"id": "a"}), _dump({"id": "drop"})
    path = tmp_path / "ledger.jsonl"
    path.write_text("not json at all\n" + good + "\n" + drop + "\n",
                    encoding="utf-8")
    jsonl.rewrite(
        path, lambda line, row: None if row.get("id") == "drop" else line)
    assert path.read_text(encoding="utf-8") == "not json at all\n" + good + "\n"


def test_rewrite_round_trips_undecodable_bytes(tmp_path):
    drop = _dump({"id": "drop"}).encode("utf-8")
    keep = b'{"id": "k", "note": "caf\xe9"}'
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"\xff\xfe junk\n" + keep + b"\n" + drop + b"\n")
    jsonl.rewrite(
        path, lambda line, row: None if row.get("id") == "drop" else line)
    assert path.read_bytes() == b"\xff\xfe junk\n" + keep + b"\n"


def test_rewrite_heals_a_split_row_when_it_writes(tmp_path):
    whole = _dump({"id": "a", "note": "l r"})
    path = tmp_path / "ledger.jsonl"
    path.write_text(whole.replace(" ", "\n") + "\n" + _dump({"id": "d"})
                    + "\n", encoding="utf-8")
    jsonl.rewrite(
        path, lambda line, row: None if row.get("id") == "d" else line)
    assert path.read_bytes() == (whole + "\n").encode("utf-8")


def test_rewrite_without_a_change_does_not_touch_the_file(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("junk\n" + _dump({"id": "a"}), encoding="utf-8")  # no \n
    before = path.read_bytes()
    assert jsonl.rewrite(path, lambda line, row: line) == 0
    assert path.read_bytes() == before


def test_rewrite_failure_leaves_the_ledger_and_no_tmp(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    original = _dump({"id": "a"}) + "\n"
    path.write_text(original, encoding="utf-8")
    real = type(path).write_text

    def failing(self, *a, **k):
        if self.name.endswith(".forget-tmp"):
            raise OSError("disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(type(path), "write_text", failing)
    with pytest.raises(OSError):
        jsonl.rewrite(path, lambda line, row: None)
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.forget-tmp"))


def test_read_rows_strict_raises_on_undecodable_and_default_does_not(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"a": "\xff"}\n')
    assert len(jsonl.read_rows(path)) == 1
    with pytest.raises(ValueError):
        jsonl.read_rows(path, errors="strict")
