"""Census: no reader compares a `project_slug` stamp to a project slug by hand.

A signed session file keeps the stamp of the bucket that wrote it, so after a
merge the stamp names a bucket that no longer exists (an alias row says where its
history lives now). A reader that decides membership with `stamp == slug` loses
those files, and the same store then shows a merged bucket's history in one
surface and not in another. Every such comparison goes through
`store.stamp_slugs` (walks) or `store.stamp_matches` (one stamp).

Whether a value is a stamp is not statically decidable, so this finds the shapes
the package actually uses: `<x>.get("project_slug")`, `<x>["project_slug"]`, a
name assigned from either inside the same function, and the recall-index column
name `row_slug`. A comparison (`==`, `!=`, `in`, `not in`) with such an operand
fails unless the other operand is a constant, a call to one of the two helpers,
or a name bound from `stamp_slugs` (or a parameter called `stamps`). A new site
fails here until it is converted, or listed below WITH its reason.
"""

import ast

from .test_read_layers import PLUGIN

PKG = PLUGIN / "daimon_briefing"
HELPERS = {"stamp_slugs", "stamp_matches"}

# (file relative to the package, enclosing function) -> why exact comparison is right.
ALLOWED = {
    ("buckets.py", "_restamped"): (
        "migration internals: rewrites exactly the files stamped with the "
        "LEGACY slug it is migrating, an alias would defeat the check"),
    ("recall.py", "_judge_rows"): (
        "`slug not in judges` is a memo-key lookup of one verdict per slug, "
        "not a membership test against a project"),
    ("recall.py", "describe_scope"): (
        "index rows carry the CANONICAL slug: recall maps every stamp through "
        "buckets.alias_map() when it rebuilds (recall.py, once per rebuild)"),
    ("cli/search.py", "_cmd_recall"): (
        "counts recall-index rows, whose slug is already canonical (see "
        "describe_scope)"),
    ("privacy.py", "_scan_recall_db"): (
        "compares recall-index rows, whose slug is already canonical (see "
        "describe_scope)"),
}


def _is_stamp_expr(n: ast.AST) -> bool:
    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get" and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == "project_slug"):
        return True
    return (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
            and n.slice.value == "project_slug")


def _has_stamp(n: ast.AST) -> bool:
    return any(_is_stamp_expr(x) for x in ast.walk(n))


def _is_helper_call(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
    return name in HELPERS


def _targets(t: ast.AST) -> set[str]:
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, (ast.Tuple, ast.List)):
        return set().union(*(_targets(e) for e in t.elts)) if t.elts else set()
    return set()


def violations(source: str) -> list[tuple[str, int, str]]:
    """(function, line, comparison) for every hand-rolled stamp comparison."""
    tree = ast.parse(source)
    out = []
    # A set bound in an enclosing function is visible to a nested one, so the
    # helper-bound names are collected file-wide.
    bound_sets = {t for n in ast.walk(tree)
                  if isinstance(n, ast.Assign) and _is_helper_call(n.value)
                  for tgt in n.targets for t in _targets(tgt)}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stamp_names = {"row_slug"}
        set_names = bound_sets | {a.arg for a in fn.args.args
                                  if a.arg == "stamps"}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                if _has_stamp(n.value):
                    for t in n.targets:
                        stamp_names |= _targets(t)

        def stampish(o: ast.AST) -> bool:
            return _is_stamp_expr(o) or (isinstance(o, ast.Name)
                                         and o.id in stamp_names)

        def safe(o: ast.AST) -> bool:
            return (isinstance(o, ast.Constant) or _is_helper_call(o)
                    or (isinstance(o, ast.Name) and o.id in set_names))

        for n in ast.walk(fn):
            if not isinstance(n, ast.Compare) or not any(
                    isinstance(o, (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
                    for o in n.ops):
                continue
            ops = [n.left, *n.comparators]
            if any(stampish(o) for o in ops) and not any(safe(o) for o in ops):
                out.append((fn.name, n.lineno, ast.unparse(n)))
    return sorted(set(out))


def test_no_reader_compares_a_project_slug_stamp_by_hand():
    found = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        for fn, line, text in violations(path.read_text(encoding="utf-8")):
            if (rel, fn) not in ALLOWED:
                found.append(f"{rel}:{line} in {fn}: {text}")
    assert not found, (
        "compare a project_slug stamp through store.stamp_slugs / "
        "store.stamp_matches so a merged-away bucket's files still belong to "
        "the project, or list the site in ALLOWED with the reason:\n"
        + "\n".join(found))


def test_every_allowed_site_still_exists_and_still_needs_the_allowance():
    live = set()
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        live |= {(rel, fn) for fn, _l, _t in violations(
            path.read_text(encoding="utf-8"))}
    assert set(ALLOWED) <= live, set(ALLOWED) - live


def test_the_census_sees_the_shapes_it_polices():
    bad = ('def f(raw, slug):\n'
           '    stamp = raw.get("project_slug")\n'
           '    a = raw.get("project_slug") == slug\n'
           '    b = raw["project_slug"] != slug\n'
           '    return stamp != slug\n')
    assert [v[1] for v in violations(bad)] == [3, 4, 5]
    good = ('def f(raw, slug, stamps):\n'
            '    mine = stamp_slugs(slug)\n'
            '    a = raw.get("project_slug") in mine\n'
            '    b = raw.get("project_slug") not in stamps\n'
            '    c = raw.get("project_slug") == ""\n'
            '    return store.stamp_matches(raw.get("project_slug"), slug)\n')
    assert violations(good) == []
