"""Anchor cognitive items to code symbols and detect drift — stdlib only.

A symbol is identified by (file, symbol) where symbol is `name` or `Class.method`.
The fingerprint is a structural hash (`ast.dump` of the def node), so it is stable
to formatting/comments/line-shift and changes only on real structural edits. No MCP,
no LLM, no network — resolution and drift checks read the project's own source.

Caveat: `ast.dump` output is stable only WITHIN a Python version. A checkpoint anchored
under one interpreter and checked under another may report a spurious "soft" drift — it
fails safe (toward verify-before-trusting, never a false "live"), and anchors are normally
resolved and checked by the same interpreter.
"""

import ast
import hashlib
from pathlib import Path

_DEF = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _find_node(tree: ast.AST, symbol: str):
    nodes = getattr(tree, "body", [])
    node = None
    for part in symbol.split("."):
        node = next(
            (n for n in nodes if isinstance(n, _DEF) and n.name == part), None
        )
        if node is None:
            return None
        nodes = getattr(node, "body", [])
    return node


def body_hash_of(source: str, symbol: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    node = _find_node(tree, symbol)
    if node is None:
        return None
    return hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()


def resolve(project_root, file: str, symbol: str) -> dict | None:
    """Snapshot an anchor for (file, symbol), or None if it can't be resolved."""
    try:
        source = (Path(project_root) / file).read_text(encoding="utf-8")
    except OSError:
        return None
    h = body_hash_of(source, symbol)
    if h is None:
        return None
    return {
        "qualified_name": f"{file}::{symbol}",
        "file": file,
        "symbol": symbol,
        "body_hash": h,
    }


def check(anchor: dict, project_root) -> str:
    """Classify drift: 'live' (unchanged), 'soft' (body changed), 'hard' (gone/unverifiable).

    Degrades on a malformed anchor (missing/non-str file or symbol) by returning
    'hard' — the offline check must never raise on hand-edited checkpoint data."""
    file = anchor.get("file")
    symbol = anchor.get("symbol")
    if not isinstance(file, str) or not isinstance(symbol, str):
        return "hard"
    try:
        source = (Path(project_root) / file).read_text(encoding="utf-8")
    except OSError:
        return "hard"
    h = body_hash_of(source, symbol)
    if h is None:
        return "hard"
    return "live" if h == anchor.get("body_hash") else "soft"


def _all_items(checkpoint: dict):
    wc = checkpoint.get("working_context") or {}
    es = checkpoint.get("epistemic_snapshot") or {}
    for key in ("open_questions", "recent_decisions"):
        yield from (wc.get(key) or [])
    for key in ("strong_beliefs", "uncertainties"):
        yield from (es.get(key) or [])
    active = wc.get("active_topic")
    if isinstance(active, dict):
        yield active


def drifted(checkpoint: dict, project_root) -> list[dict]:
    """Anchored items whose code has drifted (soft/hard); live ones omitted."""
    out = []
    for item in _all_items(checkpoint):
        a = item.get("anchored_to") if isinstance(item, dict) else None
        if not isinstance(a, dict):
            continue
        kind = check(a, project_root)
        if kind != "live":
            out.append({"item": item, "kind": kind, "anchor": a})
    return out
