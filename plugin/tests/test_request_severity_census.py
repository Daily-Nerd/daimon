"""#1348: the scale is spelled in one place. Every other reader derives from
`requests.SEVERITIES`, and the prose that teaches the levels (the skill text,
the plugin skill, the website docs in both languages) is checked against it so
a renamed level cannot leave a stale sentence behind."""

import ast
from pathlib import Path

import pytest

from daimon_briefing import requests, skill_content

PKG = Path(requests.__file__).parent
REPO = PKG.parent.parent

# Every module that reads a severity from a record or a row. `requests.py`
# owns the table and is the only one allowed to spell a level.
READERS = ("cli/request.py", "cli/lifecycle.py", "mcp_server.py",
           "mcp_tools.py", "pending.py", "briefing.py", "api.py", "view.py",
           "surfaces.py")


def _tree(rel):
    return ast.parse((PKG / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", READERS)
def test_no_other_module_spells_a_level(rel):
    levels = set(requests.SEVERITIES)
    spelled = [node.value for node in ast.walk(_tree(rel))
               if isinstance(node, ast.Constant)
               and isinstance(node.value, str) and node.value in levels]
    assert spelled == [], f"{rel} spells {spelled}; read requests.SEVERITIES"


def test_the_default_is_never_defaulted_outside_the_fold():
    """No reader indexes with a fallback: `.get("severity", ...)` anywhere but
    `requests.py` would be a second default."""
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel == "requests.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in ("severity",
                                               "severity_reason")):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


def test_the_default_is_set_in_one_place_in_requests():
    """The fold's `opened` branch is the only code that assigns the default
    onto a record."""
    tree = _tree("requests.py")
    assigned = [node for node in ast.walk(tree)
                if isinstance(node, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "severity"
                        and isinstance(v, ast.Name)
                        and v.id == "DEFAULT_SEVERITY"
                        for k, v in zip(node.keys, node.values))]
    assert len(assigned) == 1


# ---- the prose that teaches the scale ---------------------------------------

RAISED = [level for level in requests.SEVERITIES
          if level != requests.DEFAULT_SEVERITY]


def test_the_skill_text_teaches_the_flag_and_every_level_within_its_cap():
    full = skill_content.render_full()
    assert "--severity" in full and "--severity-reason" in full
    for level in RAISED:
        assert level in full
    assert len(full) <= 15300


def test_the_plugin_skill_teaches_the_verb_and_the_difference_from_blocking():
    text = (REPO / "skills" / "daimon-briefing" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "request severity" in text and "--severity" in text
    assert "--blocking" in text
    for level in RAISED:
        assert level in text


DOCS = {
    "en": REPO / "website" / "docs",
    "es": REPO / "website" / "i18n" / "es" / "docusaurus-plugin-content-docs"
    / "current",
}


@pytest.mark.parametrize("lang", sorted(DOCS))
def test_the_website_docs_cover_flag_verb_filter_and_mcp_argument(lang):
    root = DOCS[lang]
    # The how-to for severity lives in the guide; the flags and the filter are
    # generated from the CLI, the argument from the MCP schema.
    concepts = (root / "guides" / "ask-another-project.md").read_text(
        encoding="utf-8")
    cli = (root / "reference" / "cli" / "coordinate.md").read_text(encoding="utf-8")
    mcp = (root / "reference" / "mcp.md").read_text(encoding="utf-8")
    for level in RAISED:
        assert level in concepts, level
    assert "--severity" in concepts and "--severity-reason" in cli
    assert "request severity" in cli
    assert "--min-severity" in cli
    assert "min_severity" in mcp
