"""Which layer every module belongs to, and where raw reads may happen
(#1132 PR 6b).

`LAYER` names every module of `daimon_briefing` and `daimon_ui`. A module in a
`read` or `entry` layer may not read a bucket's bytes itself: every call of a
raw primitive (the store's pointer/session readers, `jsonl.read`, a JSON parse
of file content) must be listed in `RAW_READ_SITES` with the reason it is still
there. The list only shrinks: a listed site that no longer exists fails, and a
new unlisted site fails. PR 7 onward moves read modules onto `view` and deletes
entries.

Layers: ledger (owns a JSONL ledger), write (writes, or owns the store),
index (the derived recall index), view (the read view), read (renders what a
reader sees), entry (a command, server or hook entry point).

What counts as a raw read, by AST: a call of `store.<primitive>`, `jsonl.read`
/ `jsonl.read_rows` or `inspector._project_checkpoints` (as an attribute or as
a name imported from those modules), and `json.load(...)` always, `json.loads(...)` when its argument
contains `.read_text(`, `.read_bytes(`, `.read(` or `open(`. The JSON rule is
deliberately conservative: it flags a host config file as readily as a
checkpoint, and the allowlist says which is which. It misses a parse whose text
was read in an earlier statement (`raw = p.read_text(); json.loads(raw)`).
"""

import ast
from pathlib import Path

import pytest

import daimon_briefing
import daimon_ui

PLUGIN = Path(daimon_briefing.__file__).parent.parent
LAYERS = ("ledger", "view", "index", "write", "read", "entry")
STORE_PRIMITIVES = {"read_latest_body", "read_latest_result", "read_checkpoint",
                    "read_team", "items_for_project", "project_surfaces",
                    "read_own_stream_latest"}
JSONL_PRIMITIVES = {"read", "read_rows"}
JSONL_WRITERS = {"append", "append_lines", "replace", "rewrite"}
FILE_READ_CALLS = {"read_text", "read_bytes", "read"}

# `_hooks/` is excluded from the table: it holds byte-for-byte copies of the
# standalone host scripts (`hook/`) and of redact/checks_runtime/checks_host,
# kept equal by scripts/sync_hooks.py; the copies cannot import the package.
LAYER: dict[str, str] = {
    "daimon_briefing/__init__.py": "entry",  # package entry: Hermes register()
    "daimon_briefing/mcp_server.py": "entry",  # MCP stdio server
    "daimon_briefing/effects_commit.py": "entry",  # commits a read's Effects after its output
    "daimon_briefing/recall.py": "index",  # the derived recall index, rebuilt from checkpoints and ledgers
    "daimon_briefing/amendments.py": "ledger",  # owns amendments.jsonl and its fold
    "daimon_briefing/buckets.py": "ledger",  # bucket migration and its receipt ledger (migrations.jsonl)
    "daimon_briefing/checks.py": "ledger",  # folds logs/checks.jsonl, the firing log of armed checks
    "daimon_briefing/jsonl.py": "ledger",  # the JSONL substrate: append, read health, rewrite
    "daimon_briefing/ledger.py": "ledger",  # owns logs/serialize.log, the capture ledger heal parses
    "daimon_briefing/ledger_census.py": "ledger",  # per-ledger health census of a bucket
    "daimon_briefing/ledger_repair.py": "ledger",  # repairs and re-scrubs a bucket ledger
    "daimon_briefing/recall_telemetry.py": "ledger",  # owns logs/recall-delivery.jsonl
    "daimon_briefing/refutations.py": "ledger",  # owns refutations.jsonl (refutations and rulings) and its fold
    "daimon_briefing/relations.py": "ledger",  # owns relations.jsonl and its fold
    "daimon_briefing/requests.py": "ledger",  # owns requests.jsonl and its fold
    "daimon_briefing/surfaces.py": "ledger",  # registry of every store shape and its delete strategy
    "daimon_briefing/terms.py": "ledger",  # salient-term extraction shared by recall and carry; imports nothing from daimon
    "daimon_briefing/trust.py": "ledger",  # owns trust.jsonl (quarantine) and its fold
    "daimon_briefing/anchor.py": "read",  # drift scan of anchored items; no bucket reads
    "daimon_briefing/briefing.py": "read",  # builds and renders the briefing
    "daimon_briefing/display.py": "read",  # bounded display helpers for foreign text
    "daimon_briefing/marks.py": "read",  # the briefing text marks the renderers and api.parse_briefing share; stdlib only
    "daimon_briefing/hooks.py": "read",  # Hermes pre_llm_call injects a briefing
    "daimon_briefing/inspector.py": "read",  # `why`: the evidence receipt for one item
    "daimon_briefing/mcp_tools.py": "read",  # MCP tool handlers: render briefings, recall and status
    "daimon_briefing/pending.py": "read",  # the proposals-awaiting-a-human queue
    "daimon_briefing/receipts.py": "read",  # verifies and reports provenance receipts of checkpoints
    "daimon_briefing/render.py": "read",  # every human and rich renderer
    "daimon_briefing/worldcheck.py": "read",  # spot-checks checkpoint claims against the world
    "daimon_briefing/api.py": "view",  # re-exports of the read side
    "daimon_briefing/effects.py": "view",  # effects record of a read
    "daimon_briefing/view.py": "view",  # the read view
    "daimon_briefing/capture.py": "write",  # the serialize-time capture pipeline
    "daimon_briefing/carry.py": "write",  # merges unresolved items forward at write time
    "daimon_briefing/channels.py": "write",  # authority of each write channel
    "daimon_briefing/clock.py": "write",  # the one clock and id source every ledger stamp reads
    "daimon_briefing/checks_host.py": "write",  # host adapter of the check runtime
    "daimon_briefing/checks_runtime.py": "write",  # standalone check runtime, mirrored into the hooks
    "daimon_briefing/codex_hooks.py": "write",  # edits Codex hook config
    "daimon_briefing/config.py": "write",  # settings and paths; reads no checkpoint
    "daimon_briefing/configure.py": "write",  # writes ~/.daimon/env
    "daimon_briefing/field_table.py": "write",  # the checkpoint field contract and validator
    "daimon_briefing/harvest.py": "write",  # harvests host memory files into checkpoints
    "daimon_briefing/host_detect.py": "write",  # host and plugin detection for installers
    "daimon_briefing/host_mcp_caps.py": "write",  # host MCP capability table
    "daimon_briefing/kimi_hooks.py": "write",  # edits Kimi hook config
    "daimon_briefing/llm.py": "write",  # LLM client for extraction
    "daimon_briefing/multihash.py": "write",  # the vitni outputs_hash encoding, shared by receipts (mint) and the view (read)
    "daimon_briefing/normalize.py": "write",  # canonical content keys used by write gates and the view
    "daimon_briefing/policy.py": "write",  # admission gate for every write and inbound row
    "daimon_briefing/privacy.py": "write",  # residue audit: it must see raw bytes to prove a forget reached them
    "daimon_briefing/provenance.py": "write",  # quote provenance receipts bound at capture
    "daimon_briefing/redact.py": "write",  # secret scrubbing at every write boundary
    "daimon_briefing/schema.py": "write",  # ITEM_FIELDS, the one table of item sections and kinds
    "daimon_briefing/scoring.py": "write",  # effective weights used by capture and rendering
    "daimon_briefing/serializer.py": "write",  # LLM extraction and checkpoint validation
    "daimon_briefing/skill_content.py": "write",  # skill text shipped to hosts
    "daimon_briefing/skill_install.py": "write",  # installs skills into a host
    "daimon_briefing/store.py": "write",  # owns the checkpoint store; its read primitives are the readers the layers above must stop calling
    "daimon_briefing/teamproject.py": "write",  # logical project paths for the team mirror
    "daimon_briefing/teamsync.py": "write",  # git sync of the team mirror
    "daimon_briefing/testing.py": "write",  # supported test helpers for hosts (deterministic clock)
    "daimon_briefing/tool_context.py": "write",  # tool-call context capture for extraction
    "daimon_briefing/transcript.py": "write",  # host transcript loaders
    "daimon_briefing/cli/__init__.py": "entry",  # parser, dispatch and shared helpers
    "daimon_briefing/cli/__main__.py": "entry",  # python -m daimon_briefing.cli
    "daimon_briefing/cli/_cap_refusal.py": "entry",  # shared over-cap refusal text
    "daimon_briefing/cli/_hookhosts.py": "entry",  # hook host table and drift checks
    "daimon_briefing/cli/_lifecycle.py": "entry",  # no-host-named branch shared by hooks and skill
    "daimon_briefing/cli/check.py": "entry",  # check sync
    "daimon_briefing/cli/configure_cmd.py": "entry",  # configure wizard
    "daimon_briefing/cli/hooks.py": "entry",  # hooks install, list, status
    "daimon_briefing/cli/ledger_cmd.py": "entry",  # ledger repair
    "daimon_briefing/cli/serialize.py": "entry",  # serialize, write-checkpoint, heal
    "daimon_briefing/cli/skill.py": "entry",  # skill install and show
    "daimon_briefing/cli/team.py": "entry",  # team init, sync, status
    "daimon_briefing/cli/_ledger.py": "read",  # shared renderers and resolvers of the ledger verbs
    "daimon_briefing/cli/action_recall.py": "read",  # PreToolUse recall backend
    "daimon_briefing/cli/amend.py": "read",  # amend verbs
    "daimon_briefing/cli/audit.py": "read",  # audit verbs: residue and quotes
    "daimon_briefing/cli/brief.py": "read",  # brief and anchor
    "daimon_briefing/cli/handoff.py": "read",  # handoff and log
    "daimon_briefing/cli/history.py": "read",  # diff and blame
    "daimon_briefing/cli/inject.py": "read",  # recall-inject UserPromptSubmit backend
    "daimon_briefing/cli/lifecycle.py": "read",  # resolve, forget, reverify, loops, decide
    "daimon_briefing/cli/projects.py": "read",  # projects, slug, bucket migrate
    "daimon_briefing/cli/refute.py": "read",  # refute verbs
    "daimon_briefing/cli/relations_cmd.py": "read",  # relations verbs
    "daimon_briefing/cli/request.py": "read",  # request verbs
    "daimon_briefing/cli/ruling.py": "read",  # ruling verbs
    "daimon_briefing/cli/search.py": "read",  # recall, why, serve
    "daimon_briefing/cli/stats.py": "read",  # stats aggregates
    "daimon_briefing/cli/status.py": "read",  # status, verify-receipt, mcp serve
    "daimon_briefing/cli/trust.py": "read",  # trust verbs
    "daimon_ui/__init__.py": "entry",  # package marker
    "daimon_ui/__main__.py": "entry",  # viewer entry point
    "daimon_ui/reader.py": "entry",  # viewer reads: every route answers through the view, no bucket reads
    "daimon_ui/server.py": "entry",  # viewer HTTP server: routes, no bucket reads
}

# (module, qualname of the enclosing def, primitive) -> why it is still here.
RAW_READ_SITES: dict[tuple[str, str, str], str] = {
    ("daimon_briefing/cli/_hookhosts.py", "_plugin_drift", "json.loads"):
        "host plugin record (~/.claude/plugins), not a bucket path",
    ("daimon_briefing/cli/_hookhosts.py", "_plugin_manifest_version", "json.loads"):
        "daimon's own plugin manifest file, not a bucket path",
    ("daimon_briefing/cli/action_recall.py", "_cmd_action_recall", "store.read_latest_body"):
        "reads the own latest checkpoint directly; moves onto the view in PR 7",
    ("daimon_briefing/cli/amend.py", "_cmd_amend_propose", "store.read_latest_body"):
        "reads the own latest checkpoint directly; moves onto the view in PR 7",
    ("daimon_briefing/cli/audit.py", "_audit_item_source", "store.read_checkpoint"):
        "audits the provenance of the stored copy of an item; needs the raw checkpoint",
    ("daimon_briefing/cli/audit.py", "_cmd_audit_quotes", "json.loads"):
        "quote audit parses stored checkpoint files raw to check their bytes",
    ("daimon_briefing/cli/brief.py", "_cmd_anchor", "store.read_latest_body"):
        "reads the own latest checkpoint directly; moves onto the view in PR 7",
    ("daimon_briefing/cli/history.py", "_read_pointer", "json.loads"):
        "diff and blame walk pointer files; moves onto view.chain in PR 7",
    ("daimon_briefing/cli/inject.py", "_cmd_recall_inject", "store.read_latest_body"):
        "reads the own latest checkpoint directly; moves onto the view in PR 7",
    ("daimon_briefing/cli/inject.py", "_load_seen", "json.loads"):
        "the per-session seen/cooldown state file, not a checkpoint",
    ("daimon_briefing/cli/lifecycle.py", "_cmd_forget", "store.items_for_project"):
        "forget binds its target across every surface copy",
    ("daimon_briefing/cli/lifecycle.py", "_cmd_forget", "store.read_latest_body"):
        "forget binds its target in the live checkpoint; view.match in PR 9",
    ("daimon_briefing/cli/lifecycle.py", "_cmd_resolve", "store.read_latest_body"):
        "resolve binds its target in the live checkpoint; view.match in PR 9",
    ("daimon_briefing/cli/lifecycle.py", "_cmd_reverify", "store.read_latest_body"):
        "reverify binds its target in the live checkpoint; view.match in PR 9",
    ("daimon_briefing/cli/stats.py", "_stats_resolutions", "jsonl.read"):
        "counts events rows for the stats credit rollup, no content shown",
    ("daimon_briefing/cli/stats.py", "_stats_stitching", "json.loads"):
        "aggregate counters over stored checkpoints, no item text shown",
    ("daimon_briefing/cli/stats.py", "_stats_store", "json.loads"):
        "aggregate counters over stored checkpoints, no item text shown",
    ("daimon_briefing/cli/status.py", "_checkpoint_info", "json.loads"):
        "pointer envelope for status; moves onto store.read_meta in PR 7",
    ("daimon_briefing/cli/status.py", "_cmd_verify_receipt", "store.read_latest_body"):
        "verifies the raw bytes a receipt binds",
    ("daimon_briefing/inspector.py", "_item_occurrences", "inspector._project_checkpoints"):
        "why walks every retained copy of an item; moves onto view.lookup in PR 7",
    ("daimon_briefing/inspector.py", "_legacy_source", "store.read_checkpoint"):
        "legacy source lookup reads a stored session file",
    ("daimon_briefing/inspector.py", "_project_checkpoints", "store.project_surfaces"):
        "enumerates every pointer and session copy; moves onto view.chain in PR 7",
    ("daimon_briefing/inspector.py", "_read_checkpoint", "json.loads"):
        "parses each retained copy; moves onto view.chain in PR 7",
    ("daimon_briefing/receipts.py", "_ensure_pubkey", "json.loads"):
        "receipt key file, not a bucket path",
    ("daimon_briefing/receipts.py", "_load_pubkey", "json.loads"):
        "receipt key file, not a bucket path",
    ("daimon_briefing/receipts.py", "_verify_receipt", "json.loads"):
        "receipt sidecar and checkpoint bytes it binds",
    ("daimon_briefing/receipts.py", "status_line", "store.read_latest_body"):
        "receipt status of the own latest checkpoint",
    ("daimon_briefing/receipts.py", "verbatim_degraded", "json.loads"):
        "receipt sidecar next to a checkpoint",
    ("daimon_briefing/worldcheck.py", "_receipt_eligible", "store.read_checkpoint"):
        "probe bookkeeping reads a stored checkpoint",
    ("daimon_briefing/worldcheck.py", "_receipt_probes", "store.read_checkpoint"):
        "probe bookkeeping reads a stored checkpoint",
    ("daimon_briefing/worldcheck.py", "_verify_probe", "json.loads"):
        "output of the verifier subprocess, not a bucket path",
}


def modules():
    """{key: path} for every module the table must cover."""
    found = {}
    for pkg in (Path(daimon_briefing.__file__).parent,
                Path(daimon_ui.__file__).parent):
        for path in sorted(pkg.rglob("*.py")):
            if "_hooks" in path.parts or "__pycache__" in path.parts:
                continue
            found[path.relative_to(PLUGIN).as_posix()] = path
    return found


class _Visitor(ast.NodeVisitor):
    def __init__(self, imported_from):
        self.stack: list[str] = []
        self.imported_from = imported_from
        self.sites: set[tuple[str, str]] = set()

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, node):
        name = self._primitive(node)
        if name:
            self.sites.add((".".join(self.stack) or "<module>", name))
        self.generic_visit(node)

    def _primitive(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner, attr = func.value.id, func.attr
            if owner == "store" and attr in STORE_PRIMITIVES:
                return f"store.{attr}"
            if owner == "jsonl" and attr in JSONL_PRIMITIVES:
                return f"jsonl.{attr}"
            if owner == "inspector" and attr == "_project_checkpoints":
                return "inspector._project_checkpoints"
            if owner == "json" and attr == "load":
                return "json.load"
            if owner == "json" and attr == "loads" and _reads_a_file(node):
                return "json.loads"
        if isinstance(func, ast.Name):
            origin = self.imported_from.get(func.id)
            if origin == "store" and func.id in STORE_PRIMITIVES:
                return f"store.{func.id}"
            if origin == "jsonl" and func.id in JSONL_PRIMITIVES:
                return f"jsonl.{func.id}"
            if func.id == "_project_checkpoints":
                return "inspector._project_checkpoints"
        return None


def _reads_a_file(call: ast.Call) -> bool:
    for sub in ast.walk(call):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and f.attr in FILE_READ_CALLS:
                return True
            if isinstance(f, ast.Name) and f.id == "open":
                return True
    return False


def _imports(tree):
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("store", "jsonl"):
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def raw_sites(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    visitor = _Visitor(_imports(tree))
    visitor.visit(tree)
    return visitor.sites


def found_raw_sites():
    found = set()
    for key, path in modules().items():
        if LAYER.get(key) not in ("read", "entry"):
            continue
        for qualname, primitive in raw_sites(path):
            found.add((key, qualname, primitive))
    return found


def test_every_module_has_a_layer_and_every_layer_has_a_module():
    on_disk = set(modules())
    assert on_disk - set(LAYER) == set(), (
        f"modules with no layer: {sorted(on_disk - set(LAYER))}")
    assert set(LAYER) - on_disk == set(), (
        f"layers for modules that do not exist: {sorted(set(LAYER) - on_disk)}")


def test_layer_values_are_known_and_all_used():
    assert set(LAYER.values()) <= set(LAYERS)
    assert set(LAYER.values()) == set(LAYERS)


def test_the_view_layer_is_view_effects_and_api():
    view_modules = {k for k, v in LAYER.items() if v == "view"}
    assert view_modules == {"daimon_briefing/view.py",
                            "daimon_briefing/effects.py",
                            "daimon_briefing/api.py"}


def test_an_entry_module_is_never_a_write_module_and_calls_no_ledger_writer():
    for key, layer in LAYER.items():
        if layer != "entry":
            continue
        tree = ast.parse(modules()[key].read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "jsonl"
                    and node.func.attr in JSONL_WRITERS):
                pytest.fail(f"{key}:{node.lineno} writes a ledger directly")
    assert not {k for k, v in LAYER.items()
                if v == "write" and ("/cli/" in k or k.endswith("__main__.py")
                                     or k.endswith("mcp_server.py"))}


def test_the_raw_read_sites_are_exactly_the_listed_ones():
    found = found_raw_sites()
    listed = set(RAW_READ_SITES)
    assert found - listed == set(), (
        "raw read in a read/entry module that is not allowlisted: "
        f"{sorted(found - listed)}")
    assert listed - found == set(), (
        f"stale RAW_READ_SITES entries (delete them): {sorted(listed - found)}")


VIEWER_READER_MAY_IMPORT = {"view", "schema", "api", "config"}


def _daimon_briefing_imports(tree):
    """The `daimon_briefing` submodules a tree imports, by any spelling."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "daimon_briefing":
                if len(parts) > 1:
                    found.add(parts[1])
                else:
                    found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "daimon_briefing" and len(parts) > 1:
                    found.add(parts[1])
    return found


def test_the_viewer_reader_imports_only_the_view_side():
    """`reader.py` reaches the store through the view (`view`, the `schema`
    table, the `api` re-exports, `config`), never through a module that owns a
    ledger or a pointer, so it cannot grow a second judgement of what a reader
    may see."""
    tree = ast.parse(modules()["daimon_ui/reader.py"].read_text(
        encoding="utf-8"))
    imported = _daimon_briefing_imports(tree)
    assert imported, "the scan found no daimon_briefing import: it is vacuous"
    assert imported <= VIEWER_READER_MAY_IMPORT, sorted(
        imported - VIEWER_READER_MAY_IMPORT)


def test_the_viewer_reader_has_no_raw_read_site():
    """Every viewer read goes through the view: the reader is an `entry`
    module with no allowlisted raw read."""
    assert LAYER["daimon_ui/reader.py"] == "entry"
    assert [k for k in RAW_READ_SITES if k[0] == "daimon_ui/reader.py"] == []


def test_the_import_scan_sees_every_spelling():
    tree = ast.parse(
        "from daimon_briefing import config, view\n"
        "from daimon_briefing.store import read_meta\n"
        "import daimon_briefing.trust\n"
        "import json\n")
    assert _daimon_briefing_imports(tree) == {"config", "view", "store",
                                              "trust"}


def test_every_listed_site_says_why():
    assert all(reason.strip() for reason in RAW_READ_SITES.values())
    assert all(layer in ("read", "entry")
               for (key, _q, _p) in RAW_READ_SITES
               for layer in (LAYER[key],))


def test_hooks_is_excluded_because_it_mirrors_hook_scripts():
    import sys
    sys.path.insert(0, str(PLUGIN.parent / "scripts"))
    try:
        import sync_hooks
    finally:
        sys.path.pop(0)
    mirrored = {Path(dst).name for _src, dst in sync_hooks.SYNC_PAIRS
                if "plugin/daimon_briefing/_hooks/" in dst}
    on_disk = {p.name for p in (Path(daimon_briefing.__file__).parent / "_hooks"
                                ).glob("*.py")} - {"__init__.py"}
    assert on_disk == mirrored


def test_the_scan_finds_each_kind_of_raw_read():
    """Anti-vacuity: the visitor flags every primitive on a synthetic module."""
    src = (
        "import json\nfrom .store import read_checkpoint\n"
        "def f(p):\n"
        "    store.read_latest_body(); jsonl.read(p); jsonl.read_rows(p)\n"
        "    inspector._project_checkpoints(p); json.load(h)\n"
        "    json.loads(p.read_text()); json.loads(raw)\n"
        "    read_checkpoint(1)\n"
        "class C:\n"
        "    def g(self):\n"
        "        store.read_team()\n")
    tree = ast.parse(src)
    visitor = _Visitor(_imports(tree))
    visitor.visit(tree)
    assert visitor.sites == {
        ("f", "store.read_latest_body"), ("f", "jsonl.read"),
        ("f", "jsonl.read_rows"), ("f", "inspector._project_checkpoints"),
        ("f", "json.load"), ("f", "json.loads"),
        ("f", "store.read_checkpoint"), ("C.g", "store.read_team")}


# ---- import DIRECTION (#1132 PR 10b) ----------------------------------------
#
# The ledger layer sits under the readers: no ledger module imports a module of
# the read, view, index or entry layers. (It may import the write layer: the
# ledgers lean on store and config, which own the paths.) `display` is a
# stdlib-only presenter that the layer table files under read; `requests` and
# `ledger` word their notes through it, which is the only crossing, listed here
# with its reason. `jsonl` and `surfaces` are stricter: leaves that import each
# other and nothing else in the package.

UPWARD = {"read", "view", "index", "entry"}
ALLOWED_UPWARD = {
    ("daimon_briefing/requests.py", "display"):
        "stdlib-only presenter, one wording for every channel",
    ("daimon_briefing/ledger.py", "display"):
        "stdlib-only presenter, one wording for every channel",
}
LEAVES = {"daimon_briefing/jsonl.py": {"surfaces"},
          "daimon_briefing/surfaces.py": set()}


def _package_imports(path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1:
            if node.module:
                mods.add(node.module.split(".")[0])
            else:
                mods.update(a.name for a in node.names)
        elif node.level == 0 and (node.module or "").startswith(
                "daimon_briefing."):
            mods.add(node.module.split(".")[1])
    return mods


def _upward_crossings(layer_table, root) -> set:
    found = set()
    for rel, layer in layer_table.items():
        if layer != "ledger":
            continue
        for mod in _package_imports(root / rel):
            target = layer_table.get(f"daimon_briefing/{mod}.py")
            if target in UPWARD:
                found.add((rel, mod))
    return found


def test_no_ledger_module_imports_a_reader():
    assert _upward_crossings(LAYER, PLUGIN) == set(ALLOWED_UPWARD)


def test_the_direction_scan_sees_an_upward_import(tmp_path):
    (tmp_path / "daimon_briefing").mkdir()
    (tmp_path / "daimon_briefing" / "low.py").write_text(
        "def f():\n    from . import high\n")
    table = {"daimon_briefing/low.py": "ledger",
             "daimon_briefing/high.py": "read"}
    assert _upward_crossings(table, tmp_path) == {
        ("daimon_briefing/low.py", "high")}


def test_jsonl_and_surfaces_are_leaves():
    for rel, allowed in LEAVES.items():
        assert _package_imports(PLUGIN / rel) == allowed, rel
