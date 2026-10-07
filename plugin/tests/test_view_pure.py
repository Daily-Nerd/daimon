"""`view.py` and `api.py` only read (#1132 PR 6a).

An AST pass, not a runtime check: no import of a module that exists to write,
no call that writes a file, creates a directory or appends to a ledger, and no
call of a writer verb. (`list.append` is not a ledger append, so the check is
on `jsonl.<writer>` and on the verbs by name.)
"""

import ast
from pathlib import Path

import pytest

import daimon_briefing

PKG = Path(daimon_briefing.__file__).parent
MODULES = ["view.py", "api.py"]

WRITER_MODULES = {"capture", "serializer", "harvest", "ledger_repair",
                  "teamsync", "buckets", "receipts", "configure", "llm",
                  "skill_install", "codex_hooks", "kimi_hooks", "ledger"}
JSONL_WRITERS = ("append", "append_lines", "replace", "rewrite")
WRITER_CALLS = {"write_checkpoint", "append_event", "append_verification",
                "record_forget_hits", "record_bucket_root", "mkdir",
                "write_text", "write_bytes", "unlink", "rmdir", "rename",
                "open_request", "propose", "confirm", "dismiss", "release",
                "assert_ruling", "scrub_event_fields", "publish_tombstone",
                "_atomic_write", "makedirs", "touch"}


def _tree(name):
    return ast.parse((PKG / name).read_text(encoding="utf-8"))


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:
                yield node.module.split(".")[0]
            elif node.level == 1:
                for alias in node.names:
                    yield alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]


@pytest.mark.parametrize("name", MODULES)
def test_no_writer_module_is_imported(name):
    assert not (set(_imported_modules(_tree(name))) & WRITER_MODULES)


def _violations(tree):
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "mode"), None)
            if isinstance(mode, ast.Constant) and any(
                    c in str(mode.value) for c in "wax+"):
                bad.append(f"open({mode.value!r}) line {node.lineno}")
        if isinstance(func, ast.Attribute):
            if (isinstance(func.value, ast.Name) and func.value.id == "jsonl"
                    and func.attr in JSONL_WRITERS):
                bad.append(f"jsonl.{func.attr} line {node.lineno}")
            if func.attr in WRITER_CALLS:
                bad.append(f".{func.attr}() line {node.lineno}")
        if isinstance(func, ast.Name) and func.id in WRITER_CALLS:
            bad.append(f"{func.id}() line {node.lineno}")
    return bad


@pytest.mark.parametrize("name", MODULES)
def test_no_call_writes(name):
    assert _violations(_tree(name)) == []


def test_the_check_would_catch_a_writer():
    """Anti-vacuity: the same walk flags a module that writes."""
    src = ("import jsonl\nfrom . import capture\n"
           "jsonl.append(p, r)\nopen(p, 'a')\nstore.write_checkpoint(1, 2)\n"
           "d.mkdir()\nopen(p)\nitems.append(x)\n")
    tree = ast.parse(src)
    assert "capture" in set(_imported_modules(tree)) & WRITER_MODULES
    assert len(_violations(tree)) == 4        # not open(p), not items.append
