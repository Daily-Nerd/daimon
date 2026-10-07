"""`cli.<name>` is a stable seam while `cli/__init__.py` is split (#1132 PR 5):
tests, `mcp_tools`, `host_detect` and the verb modules resolve helpers through
the `cli` namespace. Every moved name stays re-exported there.

`REQUIRED` is the set found when the split started. The scans below also walk
the tree, so a NEW `cli.<name>` reference is caught the day it is written.
"""

import ast
from pathlib import Path

import pytest

from daimon_briefing import cli

PKG = Path(__file__).parent.parent / "daimon_briefing"
TESTS = Path(__file__).parent

REQUIRED = (
    "Path",
    "SLUG_ROUTE_HELP",
    "_ACTION_BUDGET",
    "_HOOK_HOSTS",
    "_INJECT_FETCH",
    "_LEAD_WIDTH",
    "_LEDGER_SKIP_RE",
    "_ORIGIN_BUDGET",
    "_RECEIPT_PROBE_USAGE_PREFIX",
    "_RESULT_ERR_RE",
    "_RESULT_OK_RE",
    "_RETENTION_WINDOW_DAYS",
    "_SLOT_WIDTH",
    "_SPAWN_RE",
    "_STALE_MIN_HITS",
    "_attach_serialize_log_handler",
    "_audit_item_source",
    "_bucket_migrate_lines",
    "_capture_alarm",
    "_checkpoint_info",
    "_choose_recall_rows",
    "_cmd_amend_list",
    "_cmd_amend_propose",
    "_cmd_amend_verdict",
    "_cmd_audit_privacy",
    "_cmd_audit_quotes",
    "_cmd_audit_quotes_deprecated",
    "_cmd_blame",
    "_cmd_check_sync",
    "_cmd_decide",
    "_cmd_diff",
    "_cmd_forget",
    "_cmd_heal",
    "_cmd_hooks_install",
    "_cmd_hooks_list",
    "_cmd_hooks_status",
    "_cmd_ledger_repair",
    "_cmd_loops",
    "_cmd_refute_add",
    "_cmd_refute_guard",
    "_cmd_refute_list",
    "_cmd_refute_overturn",
    "_cmd_refute_ratify",
    "_cmd_refute_revise",
    "_cmd_refute_search",
    "_cmd_refute_show",
    "_cmd_request_done",
    "_cmd_request_inbox",
    "_cmd_request_list",
    "_cmd_request_open",
    "_cmd_request_reply",
    "_cmd_request_revise",
    "_cmd_request_verdict",
    "_cmd_resolve",
    "_cmd_reverify",
    "_cmd_ruling_check_try",
    "_cmd_ruling_checks",
    "_cmd_ruling_list",
    "_cmd_ruling_propose",
    "_cmd_ruling_ratify",
    "_cmd_ruling_retire",
    "_cmd_ruling_revise",
    "_cmd_ruling_show",
    "_cmd_skill_install",
    "_cmd_skill_list",
    "_cmd_skill_show",
    "_cmd_skill_uninstall",
    "_cmd_status",
    "_cmd_team_init",
    "_cmd_team_status",
    "_cmd_team_sync",
    "_cmd_trust_list",
    "_cmd_trust_propose",
    "_cmd_trust_repair",
    "_cmd_trust_show",
    "_cmd_trust_verdict",
    "_codex_registration_status",
    "_compute_outstanding",
    "_crash_log_info",
    "_crash_stamp_excepthook",
    "_emit_supersede_candidates",
    "_fit_item_text",
    "_format_age",
    "_formatter_class",
    "_heal_plan",
    "_hook_drift_present",
    "_hook_file_status",
    "_hooks_status_report",
    "_hooks_target_dir",
    "_host_install_dir",
    "_host_scripts",
    "_inject_age_bucket",
    "_legacy_audit_source",
    "_load_seen",
    "_migrate_command",
    "_note_receipt_probe_usage",
    "_note_usage",
    "_outstanding_failures",
    "_parse_serialize_log",
    "_plugin_drift",
    "_plugin_drift_present",
    "_preflight_error",
    "_prompt",
    "_raw_project",
    "_refuses_caller_scope",
    "_resolve_audit_source",
    "_resolve_project",
    "_row_age_days",
    "_run_serialize",
    "_save_seen",
    "_save_seen_atomic",
    "_seen_path",
    "_session_ledger",
    "_skill_drift_present",
    "_slug_route",
    "_stats_capture",
    "_stats_resolutions",
    "_stats_retention",
    "_stats_store",
    "_stats_verification",
    "_status_health",
    "_status_ledgers",
    "_status_world",
    "_suggest_line",
    "_tail_log_info",
    "_team_briefings",
    "_topic_teaser",
    "_version_tuple",
    "_write_worldcheck_ledger",
    "age_gate_blocks",
    "anchor",
    "build_parser",
    "config",
    "cooled_origins",
    "getpass",
    "loops_lists_project",
    "main",
    "os",
    "projects_rows",
    "recall",
    "redact",
    "serializer",
    "status_payload",
    "store",
    "sys",
    "time",
    "trust_lib",
    "worldcheck",
)


def _cli_receivers(tree) -> set:
    """Every local name bound to the cli module in this file (`cli`,
    `cli_lib`, `cli_mod`, ...), whichever way it was imported."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "daimon_briefing":
            names |= {a.asname or a.name for a in node.names if a.name == "cli"}
        elif isinstance(node, ast.Import):
            names |= {a.asname for a in node.names
                      if a.name == "daimon_briefing.cli" and a.asname}
    return names


def _attrs(path: Path, receivers: set):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if receivers == {"cli"}:
        receivers = _cli_receivers(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in receivers):
            yield node.attr


def _scan():
    found = set()
    for path in TESTS.rglob("*.py"):
        found |= set(_attrs(path, {"cli"}))
    for path in (PKG / "cli").glob("*.py"):
        found |= set(_attrs(path, {"_cli"}))
    for name in ("mcp_tools.py", "host_detect.py"):
        found |= set(_attrs(PKG / name, {"cli", "_cli"}))
    return found


@pytest.mark.parametrize("name", REQUIRED)
def test_a_name_the_split_started_with_still_resolves(name):
    assert hasattr(cli, name), f"cli.{name} disappeared from the seam"


def test_every_name_referenced_through_cli_resolves():
    missing = sorted(n for n in _scan() if not hasattr(cli, n))
    assert not missing, f"referenced as cli.<name> but not on cli: {missing}"
