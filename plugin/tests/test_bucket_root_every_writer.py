"""#1337: every bucket directory has a root record from the moment it exists,
whoever created it, and a merge leaves the target with one.

`store.ensure_bucket` is the one owner of "create a bucket". A ledger writer
that made its own directory and skipped the stamp left a bucket that could not
say which directory it belongs to. The census at the bottom fails when any
module outside `store` creates a directory without being listed with a reason.
"""

import ast
import os
from pathlib import Path

import pytest

import daimon_briefing
from daimon_briefing import amendments, buckets, relations, requests, store
from daimon_briefing.surfaces import Writer

PKG = Path(daimon_briefing.__file__).parent


def _root(checkpoints: Path, project: str) -> Path:
    return checkpoints / store.project_slug(project) / "root"


def _fresh_project(tmp_path, name="fresh"):
    proj = tmp_path / name
    proj.mkdir()
    return str(proj.resolve())


@pytest.mark.parametrize("writer_name", ["request", "relation", "amendment"])
def test_a_first_write_through_each_writer_stamps_an_absolute_root(
        writer_name, tmp_path, tmp_checkpoint_dir):
    proj = _fresh_project(tmp_path, writer_name)
    row = {"ts": "2026-11-15T00:00:00Z", "k": 1}
    if writer_name == "request":
        assert requests.append(row, project_dir=proj, writer=Writer.HUMAN)
    elif writer_name == "relation":
        assert relations._append(row, project_dir=proj, writer=Writer.HUMAN)
    else:
        assert amendments.append(row, project_dir=proj, writer=Writer.HUMAN)
    root = _root(tmp_checkpoint_dir, proj)
    assert root.read_text(encoding="utf-8").strip() == proj
    assert os.path.isabs(root.read_text(encoding="utf-8").strip())


def test_a_slug_routed_write_works_and_writes_no_bogus_root(
        tmp_checkpoint_dir):
    slug = "-routed-by-slug"
    assert requests.append({"ts": "2026-11-15T00:00:00Z"}, project_dir=slug,
                           writer=Writer.HUMAN)
    bucket = tmp_checkpoint_dir / slug
    assert (bucket / "requests.jsonl").is_file()
    assert not (bucket / "root").exists(), \
        "a root record names an absolute directory, never slug text"


def test_ensure_bucket_never_stamps_slug_text(tmp_checkpoint_dir):
    bucket = store.ensure_bucket("-just-a-slug")
    assert bucket == tmp_checkpoint_dir / "-just-a-slug"
    assert bucket.is_dir()
    assert not (bucket / "root").exists()


def test_ensure_bucket_first_writer_wins(tmp_path, tmp_checkpoint_dir):
    proj = _fresh_project(tmp_path)
    bucket = store.ensure_bucket(proj)
    (bucket / "root").write_text("/first/writer\n", encoding="utf-8")
    store.ensure_bucket(proj)
    assert (bucket / "root").read_text(encoding="utf-8") == "/first/writer\n"


def test_ensure_bucket_for_an_unknown_project_is_none(tmp_checkpoint_dir):
    assert store.ensure_bucket(None) is None


def test_a_merge_leaves_the_target_stamped(tmp_path, tmp_checkpoint_dir):
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"
    link.symlink_to(real, target_is_directory=True)
    legacy = tmp_checkpoint_dir / buckets.legacy_slug(str(link))
    target = tmp_checkpoint_dir / buckets.target_slug(str(link))
    for d in (legacy, target):
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text(
            '{"ts":"2026-11-15T00:00:00Z","n":"%s"}\n' % d.name,
            encoding="utf-8")
    (legacy / "root").write_text(str(link) + "\n", encoding="utf-8")
    assert not (target / "root").exists()

    assert buckets.migrate(str(link))["mode"] == "merge"

    assert (target / "root").read_text(encoding="utf-8").strip() == \
        str(real.resolve())


def test_a_rename_restamps_the_target_from_its_resolved_directory(
        tmp_path, tmp_checkpoint_dir):
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"
    link.symlink_to(real, target_is_directory=True)
    legacy = tmp_checkpoint_dir / buckets.legacy_slug(str(link))
    legacy.mkdir(parents=True)
    (legacy / "events.jsonl").write_text(
        '{"ts":"2026-11-15T00:00:00Z"}\n', encoding="utf-8")
    (legacy / "root").write_text(str(link) + "\n", encoding="utf-8")

    assert buckets.migrate(str(link))["mode"] == "rename"

    target = tmp_checkpoint_dir / buckets.target_slug(str(link))
    assert (target / "root").read_text(encoding="utf-8").strip() == \
        str(real.resolve())


# ---- census: nothing outside store creates a directory unlisted -----------

_LOGS = "the log directory (config.log_dir), never a checkpoint bucket"
_HOST = "a host's own config or hook files, never a checkpoint bucket"
_CKPT_ROOT = ("the checkpoint root itself (migrations.jsonl), which is not a "
              "bucket and has no project directory to name")

# (module file, enclosing function) -> why this mkdir is not a bucket. A
# bucket directory comes from store.ensure_bucket; nothing else belongs here.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("__init__.py", "_note_usage"): _LOGS,
    ("identity.py", "claim"): "the claim IS the owner's create: a slug-routed bucket (no root) and the legacy stamp",
    ("identity.py", "_claim_once"): "the claim IS the owner's create: a legacy or declared bucket made then stamped",
    ("identity.py", "_stage"): "the `.birth` stage a bucket is built in, then renamed (the owner's birth)",
    ("buckets.py", "_append_record"): _CKPT_ROOT,
    ("checks_runtime.py", "log_firing"): _LOGS,
    ("_docgen.py", "write"): "the generated documentation pages in the repo's website tree, not a bucket",
    ("checks_sync.py", "_sync"): "the armed-check body directory, not a bucket",
    ("codex_hooks.py", "_save"): _HOST,
    ("codex_hooks.py", "_save_toml"): _HOST,
    ("codex_hooks.py", "install"): _HOST,
    ("codex_hooks.py", "install_mcp"): _HOST,
    ("config.py", "_note_ledger_skip"): _LOGS,
    ("configure.py", "write_env"): _HOST,
    ("effects_commit.py", "commit_error_log"): _LOGS,
    ("harvest.py", "run"): "the scar candidates directory of a repo",
    ("heal.py", "try_lock"): _LOGS,
    ("hooks.py", "_install_one"): _HOST,
    ("hooks.py", "_ledger_failure"): _LOGS,
    ("inject.py", "_save_seen"): "the injection-seen state file directory",
    ("inject.py", "_save_seen_atomic"): "the injection-seen state file directory",
    ("kimi_hooks.py", "_save"): _HOST,
    ("kimi_hooks.py", "install"): _HOST,
    ("kimi_hooks.py", "install_mcp"): _HOST,
    ("ledger.py", "_append_retry_log"): _LOGS,
    ("ledger.py", "_append_serialize_log"): _LOGS,
    ("ledger.py", "append_crash_record"): _LOGS,
    ("ledger.py", "touch_heartbeat"): _LOGS,
    ("ledger.py", "write_host_run_marker"): _LOGS,
    ("ledger.py", "write_host_skip_line"): _LOGS,
    ("ledger.py", "write_import_marker"): _LOGS,
    ("ledger_repair.py", "_scratch_store"): "a throwaway temp directory for a dry run",
    ("llm.py", "_log_backend_stderr"): _LOGS,
    ("recall.py", "rebuild"): "the recall index file directory",
    ("recall_telemetry.py", "record"): _LOGS,
    ("receipts.py", "_ensure_pubkey"): "the receipt key directory",
    ("receipts.py", "_ensure_seed"): "the receipt key directory",
    ("serialize.py", "_attach_serialize_log_handler"): _LOGS,
    ("serializer.py", "_save_chunk_cache"): "the chunk cache directory",
    ("skill_install.py", "_install_bundled"): _HOST,
    ("skill_install.py", "install"): _HOST,
    ("teamsync.py", "init"): "the team mirror root, never a project bucket",
    ("testing.py", "sandbox"): "a throwaway test sandbox",
}


def mkdir_sites(source: str, name: str) -> set[tuple[str, str]]:
    """Every `<expr>.mkdir(...)` call in `source` as (module, function)."""
    sites = set()

    def walk(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "mkdir"):
            sites.add((name, func))
        for child in ast.iter_child_nodes(node):
            walk(child, func)

    walk(ast.parse(source), "<module>")
    return sites


def _scan() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(PKG.rglob("*.py")):
        if "_hooks" in path.parts or path.name == "store.py":
            continue
        found |= mkdir_sites(path.read_text(encoding="utf-8"), path.name)
    return found


def test_the_census_flags_a_directory_made_outside_store():
    src = "def write(p):\n    p.parent.mkdir(parents=True)\n"
    assert mkdir_sites(src, "rogue.py") == {("rogue.py", "write")}


def test_no_module_outside_store_creates_an_unlisted_directory():
    unlisted = _scan() - set(ALLOWLIST)
    assert not unlisted, (
        "a directory is created outside store. A bucket directory must come "
        "from store.ensure_bucket (it stamps the root); anything else needs "
        f"a reasoned entry in ALLOWLIST: {sorted(unlisted)}")


def test_the_allowlist_holds_no_stale_entry():
    assert set(ALLOWLIST) - _scan() == set()
