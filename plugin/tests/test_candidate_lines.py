"""`display.candidate_lines`: the one candidate printer of the binding verbs
(#1132 PR 11b, D11.5). It prints visible rows, counts withheld ones and never
names a forgotten one."""

from daimon_briefing import display
from daimon_briefing.view import Withheld

POINTER = "resolve by exact id: daimon resolve <id>"
COUNT = ("{n} withheld item(s) also match; use the exact id "
         "(daimon status --suppressed lists them)")


def _row(item_id, text, matched=None, verdict=None, label="question"):
    return display.Candidate(item_id, label, text, matched, verdict)


def _w(reason):
    return Withheld("o-aaaaaaaaaaaa", "question", reason, None, "key")


def test_visible_rows_print_with_the_label_and_the_matched_value():
    rows = [_row("o-1", "first"), _row("o-2", "second", matched="other")]
    assert display.candidate_lines(rows, query="q", pointer=POINTER) == [
        "ambiguous — matches 'q'; candidates:",
        "  o-1  [question] first",
        "  o-2  [question] second — matched: other",
        POINTER]


def test_a_matched_value_equal_to_the_text_is_not_repeated():
    rows = [_row("o-1", "same", matched="same")]
    assert "matched" not in "".join(display.candidate_lines(
        rows, query="q", pointer=POINTER))


def test_no_row_at_all_says_no_item_matches_and_points():
    assert display.candidate_lines([], query="zzz", pointer=POINTER) == [
        "no item matches 'zzz'", POINTER]


def test_withheld_rows_are_counted_never_printed():
    rows = [_row("o-1", "visible"), _row("o-2", "hidden", verdict=_w("quarantine")),
            _row("o-3", "hidden too", verdict=_w("closed"))]
    lines = display.candidate_lines(rows, query="q", pointer=POINTER)
    assert lines == ["ambiguous — matches 'q'; candidates:",
                     "  o-1  [question] visible", COUNT.format(n=2), POINTER]
    assert "hidden" not in "\n".join(lines) and "o-2" not in "\n".join(lines)


def test_only_withheld_rows_say_none_is_visible_and_count_them():
    rows = [_row("o-2", "hidden", verdict=_w("quarantine"))]
    assert display.candidate_lines(rows, query="q", pointer=POINTER) == [
        "no visible item matches 'q'", COUNT.format(n=1), POINTER]


def test_a_forgotten_row_is_in_no_line_and_no_count():
    rows = [_row("o-1", "gone", verdict=_w("forgotten"))]
    assert display.candidate_lines(rows, query="q", pointer=POINTER) == [
        "no item matches 'q'", POINTER]


def test_a_closed_snapshot_prints_no_count():
    rows = [_row("o-2", "hidden", verdict=_w("closed"))]
    assert display.candidate_lines(rows, query="q", pointer=POINTER,
                                   closed=True) == [
        "no item matches 'q'", POINTER]


def test_a_count_the_caller_holds_is_added_to_the_withheld_rows():
    rows = [_row("o-1", "visible")]
    assert COUNT.format(n=3) in display.candidate_lines(
        rows, query="q", pointer=POINTER, hidden=3)
    assert display.candidate_lines([], query="q", pointer=POINTER, hidden=1) == [
        "no visible item matches 'q'", COUNT.format(n=1), POINTER]
