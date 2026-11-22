"""The stored form of a ruling's subject, verdict and scope (#1380).

Pins the exact bytes each write path puts in the ledger, so that the one
function that derives them can be changed without moving a stored value.
`revisit_when` is not pinned here: it is collapsed up front and redacted only
at the append seam, which is a different rule.
"""
import json

import pytest

from daimon_briefing import refutations

PROJECT = "/p/ruling-stored-form"
SECRET = "AKIAIOSFODNN7EXAMPLE"

CASES = [
    ("  alpha   beta \t gamma\n\n delta  ", "alpha beta gamma delta"),
    ("\n\t lead and trail \t\n", "lead and trail"),
    (f"  key   {SECRET}\t here \n", "key [redacted:aws-key] here"),
]


def _rows():
    path = refutations._path(PROJECT)
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _fields(row):
    return {name: row[name] for name in ("subject", "verdict", "scope")}


def _asserters():
    return [
        pytest.param(refutations.assert_ruling, "ruled", id="assert_ruling"),
        pytest.param(refutations.assert_refutation, "asserted",
                     id="assert_refutation"),
    ]


@pytest.mark.parametrize("assert_fn,event", _asserters())
@pytest.mark.parametrize("raw,expected", CASES)
def test_an_asserted_text_is_stored_collapsed_then_redacted(
        tmp_checkpoint_dir, assert_fn, event, raw, expected):
    assert_fn(subject=raw, verdict=raw + " v", scope=raw + " s",
              evidence=["measurement:run-1"], channel="cli-tty",
              project_dir=PROJECT)

    row = _rows()[-1]
    assert row["event"] == event
    assert _fields(row) == {
        "subject": expected, "verdict": expected + " v",
        "scope": expected + " s"}


@pytest.mark.parametrize("assert_fn,event", _asserters())
@pytest.mark.parametrize("raw,expected", CASES)
def test_a_revised_text_is_stored_collapsed_then_redacted(
        tmp_checkpoint_dir, assert_fn, event, raw, expected):
    ref_id = assert_fn(
        subject="original subject", verdict="original verdict",
        scope="original scope", evidence=["measurement:run-1"],
        channel="cli-tty", project_dir=PROJECT)

    refutations.revise(ref_id, channel="cli-tty",
                       evidence=["measurement:run-2"], subject=raw,
                       verdict=raw + " v", scope=raw + " s",
                       project_dir=PROJECT)

    row = _rows()[-1]
    assert row["event"] == "revised"
    assert _fields(row) == {
        "subject": expected, "verdict": expected + " v",
        "scope": expected + " s"}
    assert SECRET not in json.dumps(_rows())
