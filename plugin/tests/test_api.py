"""`daimon_briefing.api`: re-exports plus the briefing parser, pinned two ways
(#1132 PR 6a, PR 7b)."""

import ast
from pathlib import Path

from daimon_briefing import api, display, pending, requests, store, view

REEXPORTS = ("lookup", "match", "read_meta", "withheld_marker",
             "withheld_json", "queue", "listing", "inbox_listing",
             "project_slug")
OWNED = ("Briefing", "parse_briefing")
EXPECTED = REEXPORTS + OWNED


def test_all_is_exactly_the_declared_surface():
    assert tuple(api.__all__) == EXPECTED
    assert set(vars(api)) >= set(EXPECTED)


def test_every_name_is_the_source_object():
    assert api.lookup is view.lookup
    assert api.match is view.match
    assert api.read_meta is store.read_meta
    assert api.withheld_marker is display.withheld_marker
    assert api.withheld_json is display.withheld_json
    assert api.queue is pending.queue
    assert api.listing is requests.listing
    assert api.inbox_listing is requests.inbox_listing
    assert api.project_slug is store.project_slug


def test_only_the_briefing_parser_is_defined_in_the_module():
    """Two-way: the only public definitions are the parser and its result
    (a read function defined here would be a second implementation, not a
    re-export). No other public name exists besides `__all__`."""
    tree = ast.parse(Path(api.__file__).read_text(encoding="utf-8"))
    defined = [n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert sorted(defined) == sorted(OWNED)
    public = {n for n in vars(api) if not n.startswith("_")}
    assert public - set(EXPECTED) <= {"dataclass", "field", "ITEM_MARKS",
                                      "RULING_MARK", "WARNING_MARKERS"}
    assert set(EXPECTED) <= public
