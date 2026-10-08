"""Which modules still read the wall clock or mint a uuid on their own.

Every ledger row takes its `order` and `event_id` from `daimon_briefing.clock`
so a host test can make writes reproducible. A module in the `ledger` or
`write` layer (the table in tests/test_read_layers.py) may not call
`time.time_ns`, `time.time`, `datetime.now`, `datetime.utcnow` or
`uuid.uuid4` itself, except the sites listed in `ALLOWLIST` with the reason
each one is not a ledger stamp. The list only shrinks: it is compared by
equality, so a listed site that no longer exists fails as surely as a new
unlisted one. A site is (module, enclosing function, primitive).

`clock.py` is the one module allowed to call them, and `_hooks/` is out of
scope (standalone host script copies, see test_read_layers.py).
"""

import ast

from .test_read_layers import LAYER, modules

CENSUSED_LAYERS = ("ledger", "write")
CLOCK_MODULE = "daimon_briefing/clock.py"
BASES = {"time": {"time_ns", "time"},
         "datetime": {"now", "utcnow"},
         "uuid": {"uuid4"}}

# (module, function, primitive) -> why it does not go through the clock.
# Display and age arithmetic, file mtimes, cooldowns, backups and the
# retention cutoff are about the real machine, never about a ledger row.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("daimon_briefing/capture.py", "_session_end_stamp", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp (falls back to the transcript mtime)",
    ("daimon_briefing/capture.py", "carry_forward", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp (carry age of items against the "
        "checkpoint's own created stamp)",
    ("daimon_briefing/codex_hooks.py", "_save", "time.time"):
        "backup file suffix on a host config edit, a real-machine filename",
    ("daimon_briefing/codex_hooks.py", "_save_toml", "time.time"):
        "backup file suffix on a host config edit, a real-machine filename",
    ("daimon_briefing/kimi_hooks.py", "_save", "time.time"):
        "backup file suffix on a host config edit, a real-machine filename",
    ("daimon_briefing/kimi_hooks.py", "install_mcp", "time.time"):
        "backup file suffix on a host config edit, a real-machine filename",
    ("daimon_briefing/kimi_hooks.py", "remove_mcp", "time.time"):
        "backup file suffix on a host config edit, a real-machine filename",
    ("daimon_briefing/ledger.py", "_append_retry_log", "datetime.now"):
        "second-resolution ts on a diagnostic or stats log line, no order "
        "or event_id, never folded",
    ("daimon_briefing/ledger.py", "_stats_capture", "datetime.now"):
        "second-resolution ts on a diagnostic or stats log line, no order "
        "or event_id, never folded (the cutoff default of a stats window)",
    ("daimon_briefing/ledger.py", "heartbeat_age", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/ledger.py", "serialize_in_flight", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/ledger.py", "touch_heartbeat", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/ledger_census.py", "record_marker", "datetime.now"):
        "ts of the .ledger-census marker, an operational file and not a "
        "folded ledger row",
    ("daimon_briefing/ledger_repair.py", "_now", "datetime.now"):
        "ts of a quarantined-lines sidecar envelope written by repair, not "
        "a folded ledger row",
    ("daimon_briefing/llm.py", "_log_backend_stderr", "datetime.now"):
        "second-resolution ts on a diagnostic or stats log line, no order "
        "or event_id, never folded",
    ("daimon_briefing/privacy.py", "audit_project", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp (age of leftover files in the residue "
        "audit)",
    ("daimon_briefing/recall_telemetry.py", "_stamp", "datetime.now"):
        "second-resolution ts on a diagnostic or stats log line, no order "
        "or event_id, never folded (recall-delivery.jsonl, takes a caller "
        "`now` already)",
    ("daimon_briefing/recall_telemetry.py", "stats", "datetime.now"):
        "second-resolution ts on a diagnostic or stats log line, no order "
        "or event_id, never folded (window default)",
    ("daimon_briefing/serializer.py", "_call_and_parse", "uuid.uuid4"):
        "per-run nonce that defeats a pinned-garbage cache replay, must "
        "differ every run",
    ("daimon_briefing/serializer.py", "_save_chunk_cache", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/serializer.py", "_save_chunk_cache", "uuid.uuid4"):
        "temp file name for an atomic write",
    ("daimon_briefing/serializer.py", "verify_quotes", "datetime.now"):
        "fallback stamp when the caller threads no `now` (scar 0016: a now- "
        "consumer takes the clock)",
    ("daimon_briefing/store.py", "_reap_stale_tmps", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/store.py", "append_event", "datetime.now"):
        "checkpoint or store-event ts, second resolution, no order or "
        "event_id; the next candidate to move onto the clock if a host "
        "needs byte-identical checkpoints",
    ("daimon_briefing/store.py", "append_verification", "datetime.now"):
        "checkpoint or store-event ts, second resolution, no order or "
        "event_id; the next candidate to move onto the clock if a host "
        "needs byte-identical checkpoints",
    ("daimon_briefing/store.py", "publish_tombstone", "datetime.now"):
        "checkpoint or store-event ts, second resolution, no order or "
        "event_id; the next candidate to move onto the clock if a host "
        "needs byte-identical checkpoints",
    ("daimon_briefing/store.py", "reap_windsurf_state", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/store.py", "record_forget_hits", "datetime.now"):
        "checkpoint or store-event ts, second resolution, no order or "
        "event_id; the next candidate to move onto the clock if a host "
        "needs byte-identical checkpoints",
    ("daimon_briefing/store.py", "team_retention_cutoff", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp",
    ("daimon_briefing/store.py", "write_checkpoint", "datetime.now"):
        "checkpoint or store-event ts, second resolution, no order or "
        "event_id; the next candidate to move onto the clock if a host "
        "needs byte-identical checkpoints",
    ("daimon_briefing/teamsync.py", "_recover_wedge", "time.time"):
        "real-machine age or mtime arithmetic (cooldown, reap, retention, "
        "heartbeat), not a row stamp (age of a git lock file)",
}


def _base_name(node):
    """`time` for `time.x`, `datetime` for `datetime.x` and
    `datetime.datetime.x`; None for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "datetime" and node.attr == "datetime"):
        return "datetime"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, imported):
        self.stack: list[str] = []
        self.imported = imported
        self.sites: set[tuple[str, str]] = set()

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, node):
        primitive = None
        func = node.func
        if isinstance(func, ast.Attribute):
            base = _base_name(func.value)
            if base in BASES and func.attr in BASES[base]:
                primitive = f"{base}.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in self.imported:
            primitive = self.imported[func.id]
        if primitive:
            self.sites.add((".".join(self.stack) or "<module>", primitive))
        self.generic_visit(node)


def _imported(tree):
    """`from time import time_ns` and friends, by the name they bind."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in BASES:
            for alias in node.names:
                if alias.name in BASES[node.module]:
                    out[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}")
    return out


def found_sites():
    found = set()
    for key, path in modules().items():
        if LAYER.get(key) not in CENSUSED_LAYERS or key == CLOCK_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = _Visitor(_imported(tree))
        visitor.visit(tree)
        found |= {(key, fn, prim) for fn, prim in visitor.sites}
    return found


def test_no_ledger_or_write_module_reads_the_clock_outside_the_allowlist():
    found, listed = found_sites(), set(ALLOWLIST)
    assert found - listed == set(), (
        "new wall-clock or uuid4 call outside clock.py (use clock.now_ns / "
        f"clock.new_id, or list it with a reason): {sorted(found - listed)}")


def test_every_allowlisted_site_still_exists():
    found, listed = found_sites(), set(ALLOWLIST)
    assert listed - found == set(), (
        f"stale allowlist entries, delete them: {sorted(listed - found)}")


def test_every_allowlist_entry_has_a_reason():
    assert all(reason.strip() for reason in ALLOWLIST.values())


def test_the_ledger_stamps_and_open_request_are_off_the_wall_clock():
    sites = found_sites()
    for module in ("requests", "refutations", "trust", "relations",
                   "amendments"):
        assert not [s for s in sites
                    if s[0] == f"daimon_briefing/{module}.py"], module
