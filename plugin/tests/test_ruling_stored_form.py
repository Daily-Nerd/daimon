"""The stored form of a ruling's subject, verdict and scope (#1380).

Pins the exact bytes each write path puts in the ledger, so that the one
function that derives them can be changed without moving a stored value.
`revisit_when` is not pinned here: it is collapsed up front and redacted only
at the append seam, which is a different rule.
"""
import ast
import json
from pathlib import Path

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


# ---- census: one function states the rule ------------------------------------
#
# On the AST, never by text search (a comment that names the thing would
# satisfy a text search). Inside `refutations.py`, only `_stored_form`
# may put the whitespace collapse and the redaction of a ruling field in one
# function. The other `redact.redact_text` uses are a different rule: anchors,
# evidence and check bodies are typed tokens or patterns (redacted before
# canonicalisation, or refused when redaction would touch them). None of them
# collapses a ruling text field.

RULING_FIELDS = {"subject", "verdict", "scope"}
OWNER = "_stored_form"


def _calls_redaction(func):
    """`redact.redact_text(...)` or a bare imported `redact_text(...)`."""
    return any(
        isinstance(n, ast.Call)
        and ((isinstance(n.func, ast.Attribute) and n.func.attr == "redact_text")
             or (isinstance(n.func, ast.Name) and n.func.id == "redact_text"))
        for n in ast.walk(func))


def _is_split_join_collapse(call):
    # " ".join(x.split()) with no separator argument to split()
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "join"
            and isinstance(call.func.value, ast.Constant)
            and call.func.value.value == " " and len(call.args) == 1):
        return False
    inner = call.args[0]
    return (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "split" and not inner.args)


def _collapses_a_ruling_field(func):
    for n in ast.walk(func):
        if not isinstance(n, ast.Call):
            continue
        if _is_split_join_collapse(n):
            return True
        if isinstance(n.func, ast.Attribute) and n.func.attr == "sub" \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "_SPACE_RE":
            return True
        if isinstance(n.func, ast.Name) and n.func.id == "_text" \
                and n.args and isinstance(n.args[0], ast.Constant) \
                and n.args[0].value in RULING_FIELDS:
            return True
    return False


def _restaters(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name != OWNER and _calls_redaction(node) \
                and _collapses_a_ruling_field(node):
            yield node


def test_only_one_function_collapses_and_redacts_a_ruling_field():
    tree = ast.parse(Path(refutations.__file__).read_text(encoding="utf-8"))
    assert [f.name for f in _restaters(tree)] == []
    owner = [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == OWNER]
    assert len(owner) == 1 and _collapses_a_ruling_field(owner[0]) \
        and _calls_redaction(owner[0])


@pytest.mark.parametrize("source", [
    "def f(s):\n    s = _text('subject', s)\n    s, _ = redact.redact_text(s)\n",
    "def f(s):\n    s, _ = redact.redact_text(_SPACE_RE.sub(' ', s).strip())\n",
    "def f(v):\n    v = _text('verdict', v)\n    return redact.redact_text(v)[0]\n",
    "def f(s):\n    s = _text('scope', s)\n    s, _ = redact_text(s)\n",
    "def f(s):\n    return redact_text(' '.join(s.split()))[0]\n",
    "def f(s):\n    s = ' '.join(s.split())\n    return redact.redact_text(s)[0]\n",
])
def test_a_planted_second_copy_is_caught(source):
    assert [f.name for f in _restaters(ast.parse(source))] == ["f"]


@pytest.mark.parametrize("source", [
    # anchors and revisit_when are other rules
    "def f(a):\n    a = _text('anchor', a)\n    return redact.redact_text(a)[0]\n",
    "def f(r):\n    r = _text('revisit_when', r, required=False)\n"
    "    return redact.redact_text(r)[0]\n",
    # redaction alone, or collapse alone, is not the rule
    "def f(s):\n    return redact.redact_text(s)[0]\n",
    "def f(s):\n    return _text('subject', s)\n",
    # the owner itself
    "def _stored_form(n, v):\n    v = _text('subject', v)\n"
    "    return redact.redact_text(v)[0]\n",
])
def test_other_uses_are_not_a_second_copy(source):
    assert list(_restaters(ast.parse(source))) == []


def test_the_stored_form_is_a_fixed_point():
    # `revise` rows are redacted here and again at the append seam, and the
    # guard measures the same string a write stores: neither may move it.
    raw = f"  keep   {SECRET}\n  me  "
    once = refutations._stored_form(raw)
    assert SECRET not in once
    assert refutations._stored_form(once) == once


# ---- the minted id derives from the stored form ------------------------------

def _stored_id(raw_subject, raw_scope):
    subject = refutations._stored_form(raw_subject)
    scope = refutations._stored_form(raw_scope)
    return refutations.make_id(subject, scope)


@pytest.mark.parametrize("assert_fn,event", _asserters())
@pytest.mark.parametrize("raw,expected", CASES)
def test_the_minted_id_is_the_id_of_the_stored_text(
        tmp_checkpoint_dir, assert_fn, event, raw, expected):
    ref_id = assert_fn(subject=raw, verdict="v", scope=raw + " s",
                       evidence=["measurement:run-1"], channel="cli-tty",
                       project_dir=PROJECT)

    assert ref_id == refutations.make_id(expected, expected + " s")
    assert ref_id == refutations.prospective_id(raw, raw + " s")
    assert ref_id == _stored_id(raw, raw + " s")


def test_a_secret_bearing_text_does_not_mint_the_id_of_its_raw_form():
    raw = f"key {SECRET}"
    assert refutations.prospective_id(raw, "s") != refutations.make_id(raw, "s")
    assert refutations.prospective_id(raw, "s") == refutations.make_id(
        "key [redacted:aws-key]", "s")
