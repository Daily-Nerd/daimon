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
                  "skill_install", "codex_hooks", "kimi_hooks", "ledger",
                  "heal", "heal_host"}
# The one named exception (D23, #1179): `api.heal_all` is the contract's first
# launcher, so api.py alone may import `heal` (the lock and the detached
# `daimon heal --all` child) and `heal_host` (the verbs that call them). They
# write no cognitive content, and api.py itself still contains no writer call
# (`_violations` below). view.py gets no allowance, so the read view can never
# reach a launcher.
LAUNCHERS = {"api.py": {"heal", "heal_host"}}
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


def _forbidden_imports(name, tree):
    return (set(_imported_modules(tree)) & WRITER_MODULES) - LAUNCHERS.get(
        name, set())


@pytest.mark.parametrize("name", MODULES)
def test_no_writer_module_is_imported(name):
    assert not _forbidden_imports(name, _tree(name))


def test_only_api_may_import_the_heal_launcher():
    assert LAUNCHERS == {"api.py": {"heal", "heal_host"}}
    src = ("from .heal import plan_all\nfrom . import heal\n"
           "from .heal_host import heal_all\n")
    assert _forbidden_imports("view.py", ast.parse(src)) == {"heal", "heal_host"}
    assert _forbidden_imports("api.py", ast.parse(src)) == set()
    # the allowance is for the launchers alone: every other writer stays forbidden
    other = ast.parse("from . import heal, capture\n")
    assert _forbidden_imports("api.py", other) == {"capture"}


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
