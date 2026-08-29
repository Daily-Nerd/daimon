"""#795 stage 2: the frozen migration manifest.

A wrong Route at a migrated site returns another project's dict with the right
type and no exception (#784's failure mode), so the migration itself is pinned:
every production call to the scoped readers is enumerated by AST walk, keyed by
enclosing-function qualname (lines move, qualnames survive), and compared to
this frozen manifest. A site added, dropped, or migrated to the wrong route
fails here by name.

Part A of the same defense: four modules hold ONLY own-route reads, so the
token OWN_ELSE_GLOBAL must never appear in them at all — scoped to those
paths the rule has no false positives and keeps guarding sites added later.
"""

import ast
from pathlib import Path

PKG = Path(__file__).parent.parent / "daimon_briefing"

SCOPED = {"read_latest_body", "read_latest_result", "read_own_stream_latest"}
LEGACY = {"read_latest", "read_latest_reportable"}

# (file, qualname, callee, route source, admit source); route/admit are None
# for read_own_stream_latest, which takes no policy argument ON PURPOSE (#126:
# nothing an env var can reach may change what the persist path carries).
MANIFEST = {
    ("capture.py", "carry_forward", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/__init__.py", "_cmd_anchor", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/__init__.py", "_cmd_brief", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/__init__.py", "_cmd_brief", "read_latest_result",
     "store.Route.OWN_ELSE_GLOBAL", "store.Admit.ANY"),
    ("cli/__init__.py", "_cmd_recall_inject", "read_latest_body",
     "briefing.injection_read_route(project)", "store.Admit.ANY"),
    ("cli/__init__.py", "_print_suppressed", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/__init__.py", "_cmd_verify_receipt", "read_latest_body",
     "store.Route.OWN_ELSE_GLOBAL", "store.Admit.OWN_OR_UNROUTED"),
    ("cli/amend.py", "_cmd_amend_propose", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/lifecycle.py", "_cmd_resolve", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/lifecycle.py", "_cmd_forget", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/lifecycle.py", "_cmd_reverify", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("cli/lifecycle.py", "_cmd_loops", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("hooks.py", "pre_llm_call", "read_latest_body",
     "briefing.injection_read_route(project)", "store.Admit.ANY"),
    ("mcp_tools.py", "_brief", "read_latest_body",
     "store.Route.OWN", "store.Admit.ANY"),
    ("receipts.py", "status_line", "read_latest_body",
     "store.Route.OWN_ELSE_GLOBAL", "store.Admit.OWN_OR_UNROUTED"),
    ("store.py", "write_checkpoint", "read_own_stream_latest", None, None),
}

# Wrapper-internal calls in store.py: the legacy surface is re-expressed over
# the scoped read (stage 1) and is not a migration site.
WRAPPER_SITES = {
    ("store.py", "read_latest", "read_latest_body"),
    ("store.py", "read_latest_reportable", "read_latest_body"),
    ("store.py", "read_own_stream_latest", "read_latest_body"),
}

OWN_ONLY_MODULES = ["capture.py", "cli/lifecycle.py", "cli/amend.py", "mcp_tools.py"]


def _callee(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _kw_src(node, name, src):
    for kw in node.keywords:
        if kw.arg == name:
            return ast.get_source_segment(src, kw.value)
    return None


def _walk(names):
    for path in sorted(PKG.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(PKG))
        stack = []

        def visit(node):
            scoped = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if scoped:
                stack.append(node.name)
            if isinstance(node, ast.Call) and _callee(node) in names:
                yield rel, ".".join(stack) or "<module>", node, src
            for child in ast.iter_child_nodes(node):
                yield from visit(child)
            if scoped:
                stack.pop()

        yield from visit(ast.parse(src))


def test_no_production_call_on_the_legacy_surface():
    hits = [(rel, qual) for rel, qual, node, src in _walk(LEGACY)]
    assert hits == [], f"legacy read_latest calls remain in production: {hits}"


def test_every_scoped_call_matches_the_frozen_manifest():
    found = set()
    for rel, qual, node, src in _walk(SCOPED):
        if (rel, qual, _callee(node)) in WRAPPER_SITES:
            continue
        found.add((rel, qual, _callee(node),
                   _kw_src(node, "route", src), _kw_src(node, "admit", src)))
    assert found == MANIFEST, (
        f"missing: {sorted(MANIFEST - found)}\nunexpected: {sorted(found - MANIFEST)}")


def test_own_else_global_never_appears_in_own_only_modules():
    # AST, not text: a comment MENTIONING the route must not trip the rule
    # (scar 0054's shape — text matches are satisfied by their own prose).
    for rel in OWN_ONLY_MODULES:
        tree = ast.parse((PKG / rel).read_text(encoding="utf-8"))
        hits = [n.lineno for n in ast.walk(tree)
                if (isinstance(n, ast.Attribute) and n.attr == "OWN_ELSE_GLOBAL")
                or (isinstance(n, ast.Name) and n.id == "OWN_ELSE_GLOBAL")]
        assert hits == [], f"{rel}:{hits} must stay own-route only"
