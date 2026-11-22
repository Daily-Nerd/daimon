"""#1301: the authority rule has one home, and a new copy fails the build.

Four rules, checked on the AST (never by text search: scar 0054, a text search
is satisfied by a comment that names the thing):

1. No module outside `authority.py` reads `CHANNEL_AUTHORITY` or
   `HUMAN_EVENT_SOURCES`, by name, attribute or import. The ledger modules
   still BIND `CHANNEL_AUTHORITY` for importers that pin the name; binding is
   a store, not a read.
2. No module outside `authority.py` compares a value against a human or agent
   channel literal (`== "cli-tty"`, `in ("ui", "signed")`).
3. No module outside `cli/_observe.py` calls `isatty()`, except the named
   allow-list below, none of which decides a tier.
4. Nothing reads a row's stored `authority` field. The tier is derived from
   the channel (`authority.of_row`); the stored field is the row's claim about
   itself.

Four more cover the handsal checker pieces (#1374):

5. No module outside `authority.py` reads the vendored reason class table.
6. No module outside `authority.py` derives an outcome: `Proven`, `Refuted`
   and `Undetermined` are built there and nowhere else, so a `Proven` always
   comes from a checker answer.
7. Nothing stores a signer: the result of `performer_of` or
   `claimed_signing_time` is never a value in a dict literal, a keyword
   argument, or the right side of an assignment to a subscript or attribute.
8. Nothing orders, expires or compares by the signer's claimed time: no
   comparison and no `sorted`/`min`/`max`/`sort` call has a call to
   `claimed_signing_time` inside it. Anything that moves the claimed time
   into a variable first is outside what this can see; review catches it.
"""
import ast
from pathlib import Path

import pytest

import daimon_briefing

PACKAGE = Path(daimon_briefing.__file__).parent

TIER_NAMES = {"CHANNEL_AUTHORITY", "HUMAN_EVENT_SOURCES"}
# `channels.py` DEFINES the shared four-channel base that `authority.py`
# builds its tables from.
BASE_NAMES = {"BASE_CHANNEL_AUTHORITY"}
BASE_OWNERS = {"authority.py", "channels.py"}
CHANNEL_LITERALS = {"cli-agent", "cli-tty", "ui", "signed"}
# Calls to `isatty()` that do not decide a tier or a channel.
ISATTY_NOT_A_TIER = {
    # the Rich renderer's "is stdout a terminal" gate
    "render.py",
    # which host the process runs under (stdin AND stdout)
    "host_detect.py",
    # the configure wizard's "may I prompt" gate: it writes settings, not rows
    "cli/configure_cmd.py",
}
ISATTY_OWNER = "cli/_observe.py"


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path.relative_to(PACKAGE).as_posix(), ast.parse(
            path.read_text(encoding="utf-8"))


def _tier_name_reads(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id in names:
            yield node
        elif isinstance(node, ast.Attribute) and node.attr in names \
                and isinstance(node.ctx, ast.Load):
            yield node
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name in names for alias in node.names):
                yield node


def _literal_compares(tree):
    def literal(node):
        if isinstance(node, ast.Constant):
            return node.value in CHANNEL_LITERALS
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(literal(e) for e in node.elts)
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(
                literal(n) for n in [node.left, *node.comparators]):
            yield node


def _isatty_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "isatty":
            yield node


def _stored_authority_reads(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "authority":
            yield node
        elif isinstance(node, ast.Subscript) \
                and isinstance(node.ctx, ast.Load) \
                and isinstance(node.slice, ast.Constant) \
                and node.slice.value == "authority":
            yield node


TABLE_NAMES = {"_load_table", "_class_of", "_reason_of", "reason_classes_bytes",
               "_CLASS_OUTCOME"}
TABLE_FILE_MARKERS = ("reason-classes", "_handsal")
OUTCOME_TYPES = {"Proven", "Refuted", "Undetermined"}
SIGNER_READS = {"performer_of", "claimed_signing_time"}
CLAIMED_TIME = "claimed_signing_time"
ORDERING_CALLS = {"sorted", "min", "max", "sort"}


def _called_name(node):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _table_reads(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and any(m in node.value for m in TABLE_FILE_MARKERS):
            yield node
    yield from _tier_name_reads(tree, TABLE_NAMES)


def _outcome_constructions(tree):
    for node in ast.walk(tree):
        if _called_name(node) in OUTCOME_TYPES:
            yield node


def _signer_stores(tree):
    def is_signer(node):
        return _called_name(node) in SIGNER_READS

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for value in node.values:
                if is_signer(value):
                    yield value
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if is_signer(kw.value):
                    yield kw.value
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            if node.value is not None and is_signer(node.value) and any(
                    isinstance(t, (ast.Subscript, ast.Attribute))
                    for t in targets):
                yield node.value


def _claimed_time_ordered(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) \
                or _called_name(node) in ORDERING_CALLS:
            if any(_called_name(n) == CLAIMED_TIME for n in ast.walk(node)):
                yield node


def _where(rel, nodes):
    return [f"{rel}:{n.lineno}" for n in nodes]


# ---- the rules over the package --------------------------------------------

def test_only_authority_py_reads_the_tier_tables():
    found = []
    for rel, tree in _modules():
        if rel != "authority.py":
            found += _where(rel, _tier_name_reads(tree, TIER_NAMES))
        if rel not in BASE_OWNERS:
            found += _where(rel, _tier_name_reads(tree, BASE_NAMES))
    assert found == []


def test_nothing_compares_against_a_channel_literal_outside_authority_py():
    found = []
    for rel, tree in _modules():
        if rel != "authority.py":
            found += _where(rel, _literal_compares(tree))
    assert found == []


def test_only_the_observation_module_asks_the_terminal():
    found = {}
    for rel, tree in _modules():
        calls = list(_isatty_calls(tree))
        if calls:
            found[rel] = len(calls)
    assert set(found) == {ISATTY_OWNER} | ISATTY_NOT_A_TIER, found


def test_nothing_reads_the_stored_authority_field():
    found = []
    for rel, tree in _modules():
        found += _where(rel, _stored_authority_reads(tree))
    assert found == []


def test_only_authority_py_reads_the_handsal_table():
    found = []
    for rel, tree in _modules():
        if rel != "authority.py":
            found += _where(rel, _table_reads(tree))
    assert found == []


def test_only_authority_py_derives_an_outcome():
    found = []
    for rel, tree in _modules():
        if rel != "authority.py":
            found += _where(rel, _outcome_constructions(tree))
    assert found == []


def test_nothing_stores_a_signer():
    found = []
    for rel, tree in _modules():
        found += _where(rel, _signer_stores(tree))
    assert found == []


def test_nothing_orders_or_compares_by_the_claimed_signing_time():
    found = []
    for rel, tree in _modules():
        found += _where(rel, _claimed_time_ordered(tree))
    assert found == []


# ---- the scanners are not vacuous: a planted copy is caught ----------------

@pytest.mark.parametrize("source,scanner,args", [
    ("x = CHANNEL_AUTHORITY.get(c)", _tier_name_reads, (TIER_NAMES,)),
    ("x = refutations.CHANNEL_AUTHORITY[c]", _tier_name_reads, (TIER_NAMES,)),
    ("from .refutations import CHANNEL_AUTHORITY", _tier_name_reads,
     (TIER_NAMES,)),
    ("ok = s in store.HUMAN_EVENT_SOURCES", _tier_name_reads, (TIER_NAMES,)),
    ("if channel == 'cli-tty': pass", _literal_compares, ()),
    ("if channel in ('ui', 'signed'): pass", _literal_compares, ()),
    ("if sys.stdin.isatty(): pass", _isatty_calls, ()),
    ("x = row.get('authority')", _stored_authority_reads, ()),
    ("x = row['authority']", _stored_authority_reads, ()),
    ("t = authority._load_table()", _table_reads, ()),
    ("from .authority import _class_of", _table_reads, ()),
    ("p = open('daimon_briefing/_handsal/reason-classes.json')", _table_reads,
     ()),
    ("o = authority.Proven(jws)", _outcome_constructions, ()),
    ("o = Undetermined('x', None)", _outcome_constructions, ()),
    ("row['who'] = performer_of(p)", _signer_stores, ()),
    ("row.who = authority.performer_of(p)", _signer_stores, ()),
    ("row = {'who': performer_of(p)}", _signer_stores, ()),
    ("write(who=performer_of(p))", _signer_stores, ()),
    ("row['at'] = claimed_signing_time(p)", _signer_stores, ()),
    ("if claimed_signing_time(p) < other: pass", _claimed_time_ordered, ()),
    ("if claimed_signing_time(p) == other: pass", _claimed_time_ordered, ()),
    ("rows.sort(key=lambda r: claimed_signing_time(r.p))",
     _claimed_time_ordered, ()),
    ("x = sorted(rows, key=lambda r: authority.claimed_signing_time(r))",
     _claimed_time_ordered, ()),
    ("x = max(claimed_signing_time(a), claimed_signing_time(b))",
     _claimed_time_ordered, ()),
])
def test_a_planted_copy_of_the_rule_is_caught(source, scanner, args):
    assert list(scanner(ast.parse(source), *args))


@pytest.mark.parametrize("source,scanner,args", [
    ("CHANNEL_AUTHORITY = authority_table('trust')", _tier_name_reads,
     (TIER_NAMES,)),
    ("if channel == 'weird': pass", _literal_compares, ()),
    ("row = {'authority': a}", _stored_authority_reads, ()),
    ("# row.get('authority') and CHANNEL_AUTHORITY", _stored_authority_reads,
     ()),
    ("o = authority.recheck_outcome(r, jws)", _outcome_constructions, ()),
    ("if isinstance(o, authority.Proven): pass", _outcome_constructions, ()),
    ("# _load_table and reason-classes", _table_reads, ()),
    ("who = performer_of(p)", _signer_stores, ()),
    ("print(performer_of(p), claimed_signing_time(p))", _signer_stores, ()),
    ("if performer_of(p) == 'alice': pass", _claimed_time_ordered, ()),
    ("shown = claimed_signing_time(p)", _claimed_time_ordered, ()),
])
def test_binding_a_name_or_naming_it_in_a_comment_is_not_a_read(
        source, scanner, args):
    assert list(scanner(ast.parse(source), *args)) == []
