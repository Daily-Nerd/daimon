"""`daimon refute search` matches and ranks only over values a reader may see.

A withheld value (forgotten by another project, quarantined) must not decide
whether a row comes back or where it sits: either would answer "did the
withheld text contain this term" with one bit per query (#1211)."""

import json

import pytest

from daimon_briefing import refutation_guard, refutations, view
from tests import _masking as m

TERM = "SECRET-F"          # appears only inside the forgotten value
QTERM = "SECRET-Q"         # appears only inside the quarantined value


def _row(**over):
    kw = dict(subject="a plain subject", verdict=m.VISIBLE, scope="census",
              evidence=["measurement:replay"], channel="cli-agent",
              project_dir=m.PROJECT)
    kw.update(over)
    rid = refutations.assert_refutation(**kw)
    refutations.ratify(rid, channel="cli-tty", project_dir=m.PROJECT)
    return rid


def _cli_ids(capsys, term):
    rc, out, err = m.run(capsys, "refute", "search", term, "--json")
    assert rc == 0, err
    return [r["refutation_id"] for r in json.loads(out)], out


def _search_ids(term):
    return [r["refutation_id"]
            for r in refutation_guard.search(term, project_dir=m.PROJECT)]


@pytest.mark.parametrize("field", ["subject", "verdict", "scope", "anchor"])
def test_forgotten_field_does_not_decide_a_match(field, capsys,
                                                 tmp_checkpoint_dir):
    if field == "anchor":
        rid = _row(anchors=[m.FORGOTTEN.casefold()])
    else:
        rid = _row(**{field: m.FORGOTTEN})
    ids, _ = _cli_ids(capsys, TERM)
    assert ids == [rid], "control: visible before forget"
    m.forget()
    ids, _ = _cli_ids(capsys, TERM)
    assert rid not in ids, f"row surfaced for a query on a withheld {field}"


@pytest.mark.parametrize("field", ["subject", "verdict", "scope", "anchor"])
def test_quarantined_field_does_not_decide_a_match(field, capsys,
                                                   tmp_checkpoint_dir):
    if field == "anchor":
        rid = _row(anchors=[m.QUARANTINED.casefold()])
    else:
        rid = _row(**{field: m.QUARANTINED})
    m.quarantine()
    ids, _ = _cli_ids(capsys, QTERM)
    assert rid not in ids, f"row surfaced for a query on a quarantined {field}"


def test_visible_value_still_matches(capsys, tmp_checkpoint_dir):
    rid = _row(anchors=[m.FORGOTTEN.casefold()],
               subject="visible findable subject")
    m.forget()
    ids, out = _cli_ids(capsys, "findable")
    assert ids == [rid] and TERM not in out


def test_module_search_ignores_a_withheld_term(tmp_checkpoint_dir):
    rid = _row(subject=m.FORGOTTEN)
    m.forget()
    assert rid not in _search_ids(TERM)


def test_ranking_ignores_a_withheld_field(tmp_checkpoint_dir):
    """Both rows match on what a reader sees. Scored over the raw text the row
    with the extra withheld terms would lead; over visible text it trails."""
    withheld = "forgotten wording zeta eta"
    padded = _row(subject="alpha plain note", verdict=withheld)
    plain = _row(subject="alpha zeta note")
    assert _search_ids("alpha zeta eta")[0] == padded, "control: raw order"
    m.forget(withheld)
    assert _search_ids("alpha zeta eta") == [plain, padded]


def test_marker_text_of_a_withheld_field_does_not_match(tmp_checkpoint_dir):
    rid = _row(verdict=m.QUARANTINED)
    m.quarantine()
    marker = view.masked(m.PROJECT, "refutations.jsonl",
                         [refutations.get(rid, project_dir=m.PROJECT)]
                         )[0]["verdict"]
    assert marker != m.QUARANTINED
    assert _search_ids(marker) == []
