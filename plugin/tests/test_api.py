"""`daimon_briefing.api`: re-exports only, pinned two ways (#1132 PR 6a)."""

import ast
from pathlib import Path

from daimon_briefing import api, pending, requests, store, view

EXPECTED = ("lookup", "match", "read_meta", "queue", "listing",
            "inbox_listing", "project_slug")


def test_all_is_exactly_the_declared_surface():
    assert tuple(api.__all__) == EXPECTED
    assert set(vars(api)) >= set(EXPECTED)


def test_every_name_is_the_source_object():
    assert api.lookup is view.lookup
    assert api.match is view.match
    assert api.read_meta is store.read_meta
    assert api.queue is pending.queue
    assert api.listing is requests.listing
    assert api.inbox_listing is requests.inbox_listing
    assert api.project_slug is store.project_slug


def test_nothing_else_public_is_defined_in_the_module():
    """Two-way: no public name besides `__all__` (a function or class defined
    here would be a second implementation, not a re-export)."""
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    defined = [n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert defined == []
    public = {n for n in vars(api) if not n.startswith("_")}
    assert public == set(EXPECTED)
