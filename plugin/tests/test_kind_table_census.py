"""One table of item kinds (#1132 PR 4): nothing else may re-declare it.

`schema.ITEM_FIELDS` is the single source of the checkpoint's item sections
and kinds. A dict, tuple, list or set literal elsewhere that names two or more
of the six field keys, the six kind words or the briefing section names is a
second copy waiting to drift, unless it is a presentation table that adds
something ITEM_FIELDS cannot (an order, a label, a predicate, a result
shape), listed below with its reason.

Dict literals count by their KEYS, sequences by their elements (nested tuples
included); a `frozenset(...)`, `set(...)` or `tuple(...)` of a literal counts
as the literal. Only the outermost literal of a nest is reported.
"""

import ast
from pathlib import Path

import daimon_ui
from daimon_briefing import briefing, schema

import daimon_briefing

FIELD_KEYS = {f.key for f in schema.ITEM_FIELDS}
KIND_WORDS = {f.kind for f in schema.ITEM_FIELDS}
SECTION_NAMES = set(briefing.SECTION_ORDER) | {"verify_first"}
VOCAB = FIELD_KEYS | KIND_WORDS | SECTION_NAMES

# Whole modules that own the declaration.
OWNERS = {"schema.py", "field_table.py"}

# (module relative to its package root, name) -> why this table stays.
ALLOWLIST = {
    ("briefing.py", "SECTION_FIELD"):
        "the one hand-written presentation-section -> field map",
    ("briefing.py", "SECTION_ORDER"): "reader-need order of the sections",
    ("briefing.py", "SECTION_HEADERS"): "header text per section (labels)",
    ("briefing.py", "_BACKGROUND"): "which sections are background (predicate)",
    ("briefing.py", "_NOTE_POINTER"): "command a hidden-items note points at",
    ("briefing.py", "_TIE_SECTION_RANK"): "tie-break order, not SECTION_ORDER",
    ("briefing.py", "build"): "the briefing result shape, not a table",
    ("briefing.py", "_kept_checkpoint"):
        "rebuilds a checkpoint shape from a Selection (inverse of build)",
    ("render.py", "_SECTIONS"): "titles and colours per section (labels)",
    ("scoring.py", "TYPE_RULES"): "decay parameters keyed by scoring type",
}


def _words(node):
    """The vocabulary-bearing strings a literal holds, or None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set", "tuple") and node.args
            and isinstance(node.args[0], (ast.Tuple, ast.List, ast.Set,
                                          ast.Dict))):
        return _words(node.args[0])
    if isinstance(node, ast.Dict):
        elements = [k for k in node.keys if k is not None]
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        elements = node.elts
    else:
        return None
    out = []
    for element in elements:
        if isinstance(element, ast.Constant) and isinstance(
                element.value, str):
            out.append(element.value)
        elif isinstance(element, (ast.Tuple, ast.List)):
            out.extend(_words(element) or [])
    return out


def _is_literal(node):
    return _words(node) is not None


def _scan(path: Path):
    """[(name, lineno, hits)] for the outermost vocabulary literals."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def name_of(node):
        up = parents.get(node)
        while up is not None:
            if isinstance(up, ast.Assign) and up.targets and isinstance(
                    up.targets[0], ast.Name):
                return up.targets[0].id
            if isinstance(up, ast.AnnAssign) and isinstance(
                    up.target, ast.Name):
                return up.target.id
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return up.name
            up = parents.get(up)
        return "<module>"

    def inside_literal(node):
        up = parents.get(node)
        while up is not None:
            if _is_literal(up) and len(_hits(up)) >= 2:
                return True
            up = parents.get(up)
        return False

    found = []
    for node in ast.walk(tree):
        hits = _hits(node)
        if len(hits) >= 2 and not inside_literal(node):
            found.append((name_of(node), node.lineno, sorted(hits)))
    return found


def _hits(node):
    words = _words(node)
    return set() if words is None else {w for w in words if w in VOCAB}


def _modules():
    for pkg in (Path(daimon_briefing.__file__).parent,
                Path(daimon_ui.__file__).parent):
        for path in sorted(pkg.rglob("*.py")):
            if "_hooks" in path.parts:
                continue
            yield path


def test_no_module_re_declares_the_item_kind_table():
    offenders = []
    seen = set()
    for path in _modules():
        if path.name in OWNERS:
            continue
        for name, lineno, hits in _scan(path):
            key = (path.name, name)
            seen.add(key)
            if key not in ALLOWLIST:
                offenders.append(f"{path.name}:{lineno} {name} {hits}")
    assert not offenders, (
        "a second table of item sections/kinds: derive it from "
        "schema.ITEM_FIELDS (or allowlist it with a reason): "
        + "; ".join(offenders))
    stale = sorted(set(ALLOWLIST) - seen)
    assert not stale, f"allowlist entries that no longer match a literal: {stale}"


def test_every_scoring_type_a_field_names_is_a_type_rule():
    from daimon_briefing import scoring
    missing = [f.key for f in schema.ITEM_FIELDS
               if f.scoring_type is not None
               and f.scoring_type not in scoring.TYPE_RULES]
    assert not missing


def test_the_serializer_prompt_skeletons_name_exactly_the_item_fields():
    from daimon_briefing import serializer
    skeletons = [v for k, v in vars(serializer).items()
                 if isinstance(v, str) and '"contradictions_flagged"' in v]
    assert skeletons, "no prompt text names the item fields any more"
    for text in skeletons:
        named = {key for key in FIELD_KEYS if f'"{key}"' in text}
        assert named == FIELD_KEYS, FIELD_KEYS - named


def test_every_briefable_field_maps_to_a_section_in_section_order():
    briefable = {(f.section, f.key) for f in schema.ITEM_FIELDS if f.briefable}
    mapped = {briefing.SECTION_FIELD[s] for s in briefing.SECTION_ORDER
              if s in briefing.BRIEFABLE_SECTIONS}
    assert briefable == mapped


def test_the_derived_aliases_of_the_schema_tables_are_gone():
    """Each name was a copy or an alias of a schema view; callers read
    `schema.*` (or the walker's `field.kind`) directly."""
    import importlib
    gone = {
        "daimon_briefing.briefing": ["_KIND_BY_LIST", "BRIEFABLE_ITEM_KEYS"],
        "daimon_briefing.inspector": ["_KINDS"],
        "daimon_briefing.cli.history": ["_KINDS"],
        "daimon_briefing.store": ["_ITEM_LISTS"],
        "daimon_briefing.policy": ["_ITEM_LISTS"],
        "daimon_briefing.recall": ["_KIND_SOURCES", "_KIND_TO_TYPE"],
        "daimon_briefing.carry": ["_CARRIED_KINDS"],
    }
    left = [f"{mod}.{name}" for mod, names in gone.items()
            for name in names if hasattr(importlib.import_module(mod), name)]
    assert not left, left
