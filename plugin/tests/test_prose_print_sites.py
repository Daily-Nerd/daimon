"""Every function that reads a bucket ledger and can show it passes it through
`masked` (#1132 PR 11c, D11.8).

By AST, per function of the modules that print or serve ledger prose: if the
body calls a ledger reader (`refutations`, `requests`, `trust`, `amendments`
read functions) it must also call something named `masked`, or sit in the
short allowlist below of functions that read a record and print only a state
word, an id or a count. Every `masked` call that names its ledger by a string
literal names a registered one.

Known blind spot: the test pairs a read with a mask by FUNCTION, not by
variable. A function that masks one value and prints another passes. The read
census (`tests/test_read_sentinel.py`) is the backstop: it drives every verb
and every format against planted values and fails on any byte that gets out.
"""

import ast
from pathlib import Path

import daimon_briefing
import daimon_ui
from daimon_briefing import surfaces

PKG = Path(daimon_briefing.__file__).parent
UI = Path(daimon_ui.__file__).parent

MODULES = (
    PKG / "cli" / "_ledger.py", PKG / "cli" / "refute.py",
    PKG / "cli" / "ruling.py", PKG / "cli" / "request.py",
    PKG / "cli" / "trust.py", PKG / "cli" / "amend.py",
    PKG / "pending.py", PKG / "mcp_tools.py", UI / "server.py",
)

LEDGER_MODULES = {"refutations", "requests", "trust", "amendments"}
READERS = {"get", "listing", "records", "inbox", "inbox_listing", "join",
           "listed", "recipient_join", "search", "guard", "resolve_ruling",
           "inherited_active", "deliverable", "verdict_deliverable",
           "owed_deliverable"}

# Functions that read a record and print only a state word, an id, a count or
# a fixed sentence: nothing of a person's text leaves them.
ALLOWED_NON_PRINTING = {
    ("cli/_ledger.py", "_layer_overcap_warning"):
        "counts buckets over the cap; prints slugs and numbers",
    ("cli/ruling.py", "_checks_payload"):
        "ids, lifecycle words, hosts and counts of the armed checks",
    ("cli/request.py", "_cmd_request_done"):
        "its record is printed by _report; this reads done_pending for one "
        "fixed sentence",
    ("cli/trust.py", "_cmd_trust_verdict"):
        "prints the quarantine id and its state word",
    ("cli/amend.py", "_cmd_amend_verdict"):
        "prints amendment ids and state words",
    ("cli/amend.py", "_cmd_amend_propose"):
        "prints ids and the typed change, never the quote",
    ("cli/ruling.py", "_cmd_ruling_check_try"):
        "reads the record only to name the layer it came from; prints the "
        "outcome fields and a path",
    ("pending.py", "queue_notes"):
        "returns the notes of the join, worded by display; no record text",
    ("pending.py", "queue_with_notes"):
        "hands the join to _queue, whose lanes mask every row",
    ("pending.py", "_foreign_ledger_counts"):
        "integers per bucket, never a record (scar 0055)",
}


def _functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _calls(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            yield node


def _reads(fn):
    out = []
    for call in _calls(fn):
        f = call.func
        if (isinstance(f, ast.Attribute) and f.attr in READERS
                and isinstance(f.value, ast.Name)
                and f.value.id in LEDGER_MODULES):
            out.append(f"{f.value.id}.{f.attr}")
    return out


def _is_mask(call):
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return name in ("masked", "_masked", "_masked_lane", "_masked_record",
                    "_masked_records")


def _rel(path):
    root = UI.parent
    return path.relative_to(root).as_posix().removeprefix("daimon_briefing/")


def test_every_ledger_reader_masks_what_it_can_show():
    missing = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in _functions(tree):
            reads = _reads(fn)
            if not reads or any(_is_mask(c) for c in _calls(fn)):
                continue
            if (_rel(path), fn.name) in ALLOWED_NON_PRINTING:
                continue
            missing.append((_rel(path), fn.name, sorted(set(reads))))
    assert missing == [], missing


def test_the_allowlist_names_functions_that_exist_and_still_read():
    found = set()
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in _functions(tree):
            if _reads(fn) and not any(_is_mask(c) for c in _calls(fn)):
                found.add((_rel(path), fn.name))
    assert set(ALLOWED_NON_PRINTING) == found, (
        sorted(set(ALLOWED_NON_PRINTING) ^ found))


def test_a_masked_call_that_names_its_ledger_names_a_registered_one():
    registered = set(surfaces.bucket_ledger_names())
    seen = 0
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not _is_mask(call):
                continue
            for arg in call.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and arg.value.endswith(".jsonl")):
                    seen += 1
                    assert arg.value in registered, (_rel(path), arg.value)
    assert seen >= 8        # the scan finds the call sites it is guarding


def test_only_the_briefing_count_asks_the_queue_for_unmasked_rows():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            for kw in call.keywords:
                if (kw.arg == "masked" and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False):
                    offenders.append(path.relative_to(PKG).as_posix())
    assert offenders == ["briefing.py"], offenders


def test_every_masking_helper_the_scan_trusts_calls_masked_itself():
    """`_is_mask` accepts `masked` and a fixed set of one-line helpers; each
    helper must be defined in the scanned modules and reach `view.masked`
    (directly, or through another helper in the set)."""
    helpers = {"_masked", "_masked_lane", "_masked_record", "_masked_records"}
    found = {}
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in _functions(tree):
            if fn.name in helpers:
                names = {c.func.attr if isinstance(c.func, ast.Attribute)
                         else getattr(c.func, "id", "") for c in _calls(fn)}
                found[fn.name] = bool(names & ({"masked"} | helpers))
    assert set(found) == helpers and all(found.values()), found
