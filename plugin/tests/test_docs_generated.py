"""The generated reference pages match the code that owns them.

`reference/cli/<group>.md` is written from the argparse parsers and the tool
table in `reference/mcp.md` from the MCP server's tool schemas, both by
`daimon_briefing._docgen`. A flag or a tool argument changed without
regenerating the pages fails here, which is what makes the code the one
owner of those facts: a hand-written copy can go stale unseen, a generated
one cannot.

Regenerate after a real CLI or MCP change:
    UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py
"""
import os
import sys

import pytest

from daimon_briefing import _docgen, cli
from daimon_briefing.cli._surface import children, walk

REPO = _docgen.REPO


def test_the_committed_reference_pages_are_the_generated_ones(monkeypatch):
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    files = _docgen.generated_files()
    if os.environ.get("UPDATE_DOCS_REFERENCE"):
        _docgen.write(files)
    stale = [rel for rel, text in sorted(files.items())
             if not (REPO / rel).exists()
             or (REPO / rel).read_text(encoding="utf-8") != text]
    assert not stale, (
        "stale generated pages, regenerate with UPDATE_DOCS_REFERENCE=1: "
        + ", ".join(stale))


def test_every_top_level_command_belongs_to_exactly_one_group():
    top = {name for name, _h, _p in children(cli.build_parser())}
    placed = [c for g in _docgen.CLI_GROUPS for c in g.commands]
    assert sorted(placed) == sorted(top), (
        sorted(set(top) ^ set(placed)), "a command is missing or listed twice")


def test_generation_does_not_depend_on_the_environment(monkeypatch, tmp_path):
    first = _docgen.generated_files()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("COLUMNS", "40")
    assert _docgen.generated_files() == first


def test_every_command_of_the_tree_is_documented_on_its_group_page():
    pages = _docgen.render_cli()
    en = "\n".join(t for rel, t in pages.items()
                   if "/i18n/" not in rel and rel.startswith("website/docs/"))
    for path, _p in walk(cli.build_parser()):
        if path:
            assert f"## `daimon {' '.join(path)}`" in en or \
                f"`daimon {' '.join(path)}`" in en, path


def test_spanish_pages_keep_the_english_flag_text_under_a_spanish_header():
    pages = _docgen.render_cli()
    es = pages[
        "website/i18n/es/docusaurus-plugin-content-docs/current/"
        "reference/cli/coordinate.md"]
    en = pages["website/docs/reference/cli/coordinate.md"]
    assert "# Coordinar" in es and "# Coordinate" in en
    assert "what you are asking that project to do" in es


@pytest.mark.parametrize("raw, cell", [
    ("a | b", "a \\| b"),
    ("use <slug> here", "use &lt;slug&gt; here"),
    ("keep `<slug>|x` as is", "keep `<slug>\\|x` as is"),
    ("{x} and *.md", "&#123;x&#125; and \\*.md"),
    ("two\n  lines", "two lines"),
])
def test_table_cells_are_safe_for_markdown_and_mdx(raw, cell):
    assert _docgen.cell(raw) == cell


def test_the_mcp_table_marks_arguments_only_a_cross_project_grant_offers():
    table = _docgen.mcp_table("en")
    assert "`daimon_recall`" in table and "`requests_inbox`" in table
    assert "all_projects" in table and "DAIMON_MODEL_CROSS_PROJECT" in table
    assert sys.modules["daimon_briefing.mcp_server"]


def test_no_command_help_promises_a_future_release():
    # Help text is the one owner of a flag's description, and the generated
    # reference publishes it as is. "It goes in the next release" is true on
    # the day it is written and false from the release on, with nothing to
    # notice.
    promises = []
    for path, parser in walk(cli.build_parser()):
        for action in parser._actions:
            text = " ".join((action.help or "").split()).lower()
            if "next release" in text or "future release" in text:
                promises.append(" ".join(path) + " " + "/".join(action.option_strings))
    assert not promises, promises


def test_the_surface_reads_every_kind_of_argument():
    import argparse

    from daimon_briefing.cli._surface import arguments
    p = argparse.ArgumentParser(prog="x")
    p.add_argument("--on", action="store_true", default=True, help="a\n  flag")
    p.add_argument("--many", nargs="*", help="several")
    p.add_argument("--hidden", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version="1")
    p.add_argument("target", nargs="?", help="one")
    got = {a.flags or (a.value,): a for a in arguments(p)}
    assert list(got)[0] == ("[target]",)          # positionals first
    assert got[("--on",)].default == "true" and got[("--on",)].help == "a flag"
    assert got[("--many",)].value == "[MANY]"
    assert ("--hidden",) not in got and ("--version",) not in got


def test_write_creates_the_pages_and_a_page_without_markers_is_an_error(
        tmp_path, monkeypatch):
    monkeypatch.setattr(_docgen, "REPO", tmp_path)
    _docgen.write({"website/docs/reference/cli/x.md": "text\n"})
    assert (tmp_path / "website/docs/reference/cli/x.md").read_text() == "text\n"
    bare = tmp_path / "website/docs/reference/mcp.md"
    bare.write_text("no markers here\n")
    with pytest.raises(SystemExit):
        _docgen._with_mcp_table("en", "website/docs/reference/mcp.md")
