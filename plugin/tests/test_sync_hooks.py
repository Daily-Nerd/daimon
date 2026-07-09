"""#149: scripts/sync_hooks.py copies the intentionally-duplicated hook-shipped
files from their canonical sources, driven by the same manifest the drift guard
in test_hooks_install.py reads. --check reports drift and writes nothing."""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sync_hooks  # noqa: E402


def _build_tree(root, edits=None):
    # Materialize an in-sync copy of the mirror topology under `root`: each
    # canonical source gets deterministic content, each copy starts identical.
    srcs = {src: f"# canonical {src}\n".encode() for src, _ in sync_hooks.SYNC_PAIRS}
    for src, dst in sync_hooks.SYNC_PAIRS:
        (root / src).parent.mkdir(parents=True, exist_ok=True)
        (root / src).write_bytes(srcs[src])
        (root / dst).parent.mkdir(parents=True, exist_ok=True)
        (root / dst).write_bytes(srcs[src])
    if edits:
        for rel, data in edits.items():
            (root / rel).write_bytes(data)
    return root


def test_check_clean_tree_exits_zero(tmp_path):
    root = _build_tree(tmp_path)
    assert sync_hooks.check(root) == 0


def test_check_detects_introduced_drift(tmp_path):
    src, _ = sync_hooks.SYNC_PAIRS[0]
    root = _build_tree(tmp_path, edits={src: b"# edited canonical\n"})
    assert sync_hooks.check(root) == 1


def test_sync_repairs_drift_to_byte_identity(tmp_path):
    src, _ = sync_hooks.SYNC_PAIRS[0]
    root = _build_tree(tmp_path, edits={src: b"# edited canonical\n"})
    assert sync_hooks.sync(root) == 0
    for s, d in sync_hooks.SYNC_PAIRS:
        assert (root / d).read_bytes() == (root / s).read_bytes()
    assert sync_hooks.check(root) == 0


def test_sync_clean_tree_writes_nothing(tmp_path):
    root = _build_tree(tmp_path)
    before = {d: (root / d).stat().st_mtime_ns for _, d in sync_hooks.SYNC_PAIRS}
    assert sync_hooks.sync(root) == 0
    after = {d: (root / d).stat().st_mtime_ns for _, d in sync_hooks.SYNC_PAIRS}
    assert before == after


def test_main_check_flag_reads_module_repo_root(tmp_path, monkeypatch):
    root = _build_tree(tmp_path)
    monkeypatch.setattr(sync_hooks, "REPO_ROOT", root)
    assert sync_hooks.main(["--check"]) == 0
    src, _ = sync_hooks.SYNC_PAIRS[0]
    (root / src).write_bytes(b"# drift\n")
    assert sync_hooks.main(["--check"]) == 1


def test_check_against_real_repo_is_clean():
    # Ties the manifest's relative paths to the real tree: a wrong path raises
    # here, and any live drift fails just like the byte-identity guards.
    assert sync_hooks.check(sync_hooks.REPO_ROOT) == 0
