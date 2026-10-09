"""`surfaces.map_prose`: the writing sibling of `prose_values` (#1132 PR 11c).

It walks the same declared paths, plus a list-of-dict segment (`replies[]`),
and hands each non-blank string to a mapper. The row it returns is a deep copy.
"""

import copy

from daimon_briefing import surfaces

fp = surfaces.FieldPath


def _upper(text, _path):
    return text.upper()


def test_a_scalar_is_mapped_and_other_keys_are_untouched():
    row = {"a": "x", "keep": "y"}
    assert surfaces.map_prose((fp(("a",)),), row, _upper) == {
        "a": "X", "keep": "y"}


def test_a_nested_path_is_mapped():
    row = {"check": {"match": "m", "body": "b"}}
    out = surfaces.map_prose((fp(("check", "match")),), row, _upper)
    assert out == {"check": {"match": "M", "body": "b"}}


def test_a_list_member_is_mapped_and_a_none_drops_it():
    row = {"evidence": ["keep", "drop", "also"]}
    out = surfaces.map_prose(
        (fp(("evidence",), True),), row,
        lambda t, _p: None if t == "drop" else t.upper())
    assert out == {"evidence": ["KEEP", "ALSO"]}


def test_a_none_on_a_scalar_blanks_it():
    out = surfaces.map_prose((fp(("a",)),), {"a": "x"}, lambda t, _p: None)
    assert out == {"a": ""}


def test_a_list_of_dicts_segment_walks_each_dict():
    row = {"replies": [{"note": "one", "ts": 1}, {"note": "two"},
                       {"other": "z"}, "stray"]}
    out = surfaces.map_prose((fp(("replies[]", "note")),), row, _upper)
    assert out["replies"][0] == {"note": "ONE", "ts": 1}
    assert out["replies"][1] == {"note": "TWO"}
    assert out["replies"][2] == {"other": "z"}
    assert out["replies"][3] == "stray"


def test_a_list_inside_a_list_of_dicts_is_mapped_per_dict():
    row = {"replies": [{"evidence": ["a", "b"]}, {"evidence": ["c"]}]}
    out = surfaces.map_prose(
        (fp(("replies[]", "evidence"), True),), row,
        lambda t, _p: None if t == "b" else t.upper())
    assert out["replies"] == [{"evidence": ["A"]}, {"evidence": ["C"]}]


def test_a_blank_string_never_reaches_the_mapper():
    seen = []
    row = {"a": "  ", "b": ["", "x", "   "], "c": {"d": ""}}
    prose = (fp(("a",)), fp(("b",), True), fp(("c", "d")))
    out = surfaces.map_prose(prose, row, lambda t, p: seen.append(t) or t)
    assert seen == ["x"]
    assert out == row


def test_the_mapper_is_told_which_declared_path_it_is_on():
    seen = []
    prose = (fp(("a",)), fp(("e",), True))
    surfaces.map_prose(prose, {"a": "x", "e": ["y"]},
                       lambda t, p: seen.append(p) or t)
    assert seen == [fp(("a",)), fp(("e",), True)]


def test_the_row_is_deep_copied_never_mutated():
    row = {"a": "x", "evidence": ["k"], "replies": [{"note": "n"}],
           "check": {"match": "m"}}
    before = copy.deepcopy(row)
    prose = (fp(("a",)), fp(("evidence",), True), fp(("replies[]", "note")),
             fp(("check", "match")))
    out = surfaces.map_prose(prose, row, _upper)
    assert row == before
    out["replies"][0]["note"] = "changed"
    out["evidence"].append("more")
    assert row == before


def test_an_unknown_or_mistyped_path_is_tolerated():
    row = {"a": 3, "b": "notalist", "c": ["x"], "d": {"e": "notadict"},
           "replies": "nope"}
    prose = (fp(("missing",)), fp(("a",)), fp(("b",), True),
             fp(("c", "d")), fp(("d", "e", "f")), fp(("replies[]", "note")),
             fp(("nothing[]", "note")))
    assert surfaces.map_prose(prose, row, _upper) == row


def test_non_string_list_members_are_kept_as_they_are():
    out = surfaces.map_prose((fp(("e",), True),), {"e": [3, "x", None]}, _upper)
    assert out == {"e": [3, "X", None]}


def test_prose_values_still_reads_the_same_declared_paths():
    row = {"a": " x ", "b": ["y", "", 3, "z"], "c": {"d": "w"}}
    prose = (fp(("a",)), fp(("b",), True), fp(("c", "d")))
    assert surfaces.prose_values(prose, row) == [" x ", "y", "z", "w"]


def test_a_canonical_path_string_names_the_policy_key():
    assert surfaces.path_string(fp(("evidence",), True)) == "evidence[]"
    assert surfaces.path_string(fp(("check", "match"))) == "check.match"
    assert surfaces.path_string(fp(("replies[]", "note"))) == "replies[].note"
    assert surfaces.path_string(
        fp(("revision_proposed", "evidence"), True)
    ) == "revision_proposed.evidence[]"
