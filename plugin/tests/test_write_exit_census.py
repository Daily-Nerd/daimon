"""Every write exit says who is writing (#1132 PR 10b, D10.3).

By AST: no call of `jsonl.append` / `jsonl.append_lines` without a `posture`,
and no call of a module appender (or `store.write_checkpoint`, `checks.sync`)
without a `writer`. A new appender that skips the registry's write column
fails here, not in the field. The scanner has a negative control: it is run
on source that breaks each rule.
"""

import ast
from pathlib import Path

import daimon_briefing
import daimon_ui

PACKAGES = (Path(daimon_briefing.__file__).parent,
            Path(daimon_ui.__file__).parent)

# Module-qualified appenders that must carry `writer=`.
WRITER_CALLS = {
    ("store", "append_event"), ("store", "write_checkpoint"),
    ("requests", "append"), ("amendments", "append"), ("trust", "append"),
    ("refutations", "append"), ("relations", "_append"),
    ("checks", "sync"), ("checks", "sync_layers"), ("checks", "_sync"),
}
# The same appenders called by their bare name inside their own module.
BARE_WRITER_CALLS = {
    "requests.py": {"append", "_sync_checks"},
    "amendments.py": {"append"},
    "trust.py": {"append"},
    "refutations.py": {"append", "_sync_checks"},
    "relations.py": {"_append"},
}
# The exits that take a `posture`.
POSTURE_CALLS = {("jsonl", "append"), ("jsonl", "append_lines")}
# Cures say PROCEED outright; only these modules may (a forget's policy
# tombstone, a bucket migration). Everything else judges the ledger.
BARE_PROCEED_ALLOWED = {"refutations.py", "buckets.py"}


# Append-only files the package writes (or whose writer ships in it) WITHOUT
# going through `jsonl.append*`, each with its reason; the registry test lists
# the same exemption beside its write table.
EXEMPT_APPENDERS = {
    "logs/checks.jsonl": "written by the stdlib-only hook runtime "
                         "(checks_runtime.log_firing), append-only, reader "
                         "skips bad rows",
}


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _key(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Name):
            return base.id, f.attr
        if isinstance(base, ast.Attribute):
            return base.attr, f.attr
    if isinstance(f, ast.Name):
        return None, f.id
    return None, None


def violations(source: str, filename: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    bare = BARE_WRITER_CALLS.get(Path(filename).name, set())
    for node in _calls(tree):
        key = _key(node)
        kws = {k.arg for k in node.keywords}
        if key in POSTURE_CALLS and "posture" not in kws:
            found.append(f"{filename}:{node.lineno}: {key[0]}.{key[1]} "
                         "without posture")
        if key in WRITER_CALLS and "writer" not in kws:
            found.append(f"{filename}:{node.lineno}: {key[0]}.{key[1]} "
                         "without writer")
        if key[0] is None and key[1] in bare and "writer" not in kws:
            found.append(f"{filename}:{node.lineno}: {key[1]}() "
                         "without writer")
        if key == ("jsonl", "append_as") and len(node.args) < 3 \
                and "writer" not in kws:
            found.append(f"{filename}:{node.lineno}: jsonl.append_as "
                         "without a writer")
        if key[1] == "append_lines" and key[0] == "jsonl":
            for k in node.keywords:
                if (k.arg == "posture" and isinstance(k.value, ast.Attribute)
                        and k.value.attr == "PROCEED"
                        and Path(filename).name not in BARE_PROCEED_ALLOWED):
                    found.append(f"{filename}:{node.lineno}: a bare PROCEED "
                                 "outside a cure module")
    return found


def _scan_package() -> list[str]:
    out = []
    for root in PACKAGES:
        for path in sorted(root.rglob("*.py")):
            if "_hooks" in path.parts:
                continue
            out += violations(path.read_text(encoding="utf-8"),
                              str(path.relative_to(root.parent)))
    return out


def test_every_write_exit_in_the_package_says_who_is_writing():
    assert _scan_package() == []


# ---- negative controls: the scanner sees each kind of omission -------------

def test_the_scanner_flags_a_ledger_append_without_a_posture():
    assert violations("jsonl.append(p, row)\n", "x.py")
    assert violations("jsonl.append_lines(p, rows, lock=False)\n", "x.py")
    assert not violations("jsonl.append(p, row, posture=P)\n", "x.py")


def test_the_scanner_flags_a_module_appender_without_a_writer():
    for call in ("store.append_event('i', 's')",
                 "store.write_checkpoint('s', {})",
                 "requests.append(row)", "amendments.append(row)",
                 "trust.append(row)", "refutations.append(row)",
                 "relations._append(row)", "checks.sync(p)",
                 "checks.sync_layers(p)", "self.store.append_event('i','s')"):
        assert violations(call + "\n", "x.py"), call
    assert not violations("store.append_event('i', 's', writer=W)\n", "x.py")


def test_the_scanner_flags_an_unqualified_appender_inside_its_own_module():
    assert violations("def f():\n    return append(row)\n", "requests.py")
    assert violations("def f():\n    return _append(row)\n", "relations.py")
    assert violations("def f():\n    _sync_checks(p)\n", "refutations.py")
    assert not violations("def f():\n    return append(row, writer=W)\n",
                          "requests.py")
    # The same bare name elsewhere is somebody else's function.
    assert not violations("def f():\n    return append(row)\n", "other.py")


def test_the_scanner_flags_a_bare_proceed_outside_the_cure_modules():
    src = "jsonl.append_lines(p, rows, posture=WritePosture.PROCEED)\n"
    assert violations(src, "capture.py")
    assert not violations(src, "buckets.py")
    assert not violations(src, "refutations.py")


def test_the_scanner_flags_append_as_without_a_writer():
    assert violations("jsonl.append_as(path, row)\n", "x.py")
    assert not violations("jsonl.append_as(path, row, writer)\n", "x.py")


def test_the_exempt_appenders_are_the_ones_the_registry_exempts():
    from tests.test_write_posture_registry import WRITE_EXEMPT
    assert set(EXEMPT_APPENDERS) == set(WRITE_EXEMPT)
    # The runtime really appends outside the package's exits.
    runtime = Path(daimon_briefing.__file__).parent / "checks_runtime.py"
    assert "jsonl" not in runtime.read_text(encoding="utf-8").split(
        "def log_firing", 1)[1].split("\ndef ", 1)[0]
