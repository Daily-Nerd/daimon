"""Dogfood CLI — works WITHOUT hermes, on a plain text/markdown transcript.

    daimon serialize <transcript-file>   transcript -> checkpoint (+latest)
    daimon brief                          latest checkpoint -> briefing on stdout
    daimon recall <query...>              FTS5 search over local + team
                                         checkpoint history (derived index)
    daimon status [--project DIR] [--json]
                                         checkpoint presence/age + last
                                         serialize outcome from the log
    daimon heal [--force]                 re-serialize the most recent
                                         FAILED session if safe (#26);
                                         --force ignores a prior retry
                                         marker (#15)
    daimon configure [--backend ...]     detect the resolved LLM backend
                                         and fill gaps in ~/.daimon/env
    daimon write-checkpoint [--project DIR] [--source S]
                                         store a checkpoint read as JSON on
                                         stdin (the #23 introspection path)
"""

import argparse
import functools
import getpass  # noqa: F401 — tests patch cli.getpass.getpass
import os
import sys
import time  # noqa: F401 — tests reach it as cli.time
import traceback
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401 — tests reach it as cli.Path

from .. import amendments, anchor, briefing, buckets, capture, carry, config, configure, harvest, inspector, jsonl, ledger, ledger_census, llm, normalize, privacy, provenance, recall, recall_telemetry, receipts, redact, refutations, render, requests, schema, serializer, store, teamsync, transcript, worldcheck  # noqa: F401 — several are re-exported for compat only (#708): `cli.<name>` is a stable seam
# Aliased: `trust` below (from . import (..., trust)) already binds the
# `cli.trust` VERB submodule at this scope — this is the LIBRARY ledger
# module (daimon_briefing.trust). cli/brief.py reads it directly now; the
# name stays here because tests reach it as `cli.trust_lib`.
from .. import trust as trust_lib  # noqa: F401
from .. import __version__
from ..surfaces import Writer

# The serialize.log ledger subsystem lives in ledger.py (#147 + #162, pure
# moves). EVERY moved name is re-imported here — including the ones cli.py no
# longer calls itself — because `cli.<name>` is a stable seam: tests and host
# hooks resolve the ledger through this module.
from ..ledger import (
    AUTO_BRIEF_HOSTS,  # noqa: F401 — re-exported for compat
    _HEAL_SKIP_REASON,  # noqa: F401 — re-exported for compat
    _HEAL_TRANSCRIPT_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_OK_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_PROJECT_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_SKIP_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_SPAWN_TRANSCRIPT_RE,  # noqa: F401 — re-exported for compat
    _RESULT_ERR_RE,  # noqa: F401 — re-exported for compat
    _RESULT_OK_RE,  # noqa: F401 — re-exported for compat
    _SPAWN_RE,  # noqa: F401 — re-exported for compat
    _STATS_HOST_RE,  # noqa: F401 — re-exported for compat
    _USAGE_STAMP_FMT,  # noqa: F401 — re-exported for compat
    _append_retry_log,  # noqa: F401 — re-exported for compat
    _append_serialize_log,  # noqa: F401 — re-exported for compat
    _compute_outstanding,  # noqa: F401 — re-exported for compat
    _format_age,  # noqa: F401 — re-exported for compat
    _heal_plan,  # noqa: F401 — re-exported for compat
    _outstanding_failures,  # noqa: F401 — re-exported for compat
    _parse_serialize_log,  # noqa: F401 — re-exported for compat
    _parse_stamp,  # noqa: F401 — re-exported for compat
    _session_ledger,  # noqa: F401 — re-exported for compat
    _spawns_in_window,  # noqa: F401 — re-exported for compat
    _spawns_in_window_count,  # noqa: F401 — re-exported for compat
    _stats_capture,  # noqa: F401 — re-exported for compat
)

# Module-level seam so tests can inject a fake LLM client.
_chat = llm.chat


def _formatter_class():
    """argparse help formatter: RichHelpFormatter-family when rich-argparse
    (daimon[pretty]) is importable, else the stock formatter everywhere
    already used it. Unlike render.supports_rich(), this needs no TTY gate of
    its own — rich's Console auto-detects a non-terminal stream, so `--help`
    degrades to plain text automatically when piped or redirected. It DOES
    need to honor the same DAIMON_PLAIN/NO_COLOR opt-outs supports_rich checks
    (same truthiness semantics), because rich-argparse's own Console has no
    idea what DAIMON_PLAIN means — left ungated, `--help` would ignore a
    user's explicit plain-mode request while every other command honors it."""
    if os.environ.get("DAIMON_PLAIN", "").strip().lower() in render._TRUTHY:
        return argparse.RawDescriptionHelpFormatter
    if os.environ.get("NO_COLOR") is not None:
        return argparse.RawDescriptionHelpFormatter
    try:
        from rich_argparse import RawDescriptionRichHelpFormatter
        return RawDescriptionRichHelpFormatter
    except ImportError:
        return argparse.RawDescriptionHelpFormatter


def _prompt(question: str) -> str:
    """Raw interactive prompt — a tiny seam so tests can monkeypatch input."""
    return input(question).strip()


def _resolve_project(arg, *, for_write: bool = False) -> str:
    """Project dir for routing: explicit --project, else DAIMON_PROJECT_DIR, else cwd.

    Only the PRECEDENCE lives here. The resolution itself is
    `config.resolve_project_dir` — absolute, symlinks collapsed, then the git
    toplevel (#74) — which the library ledger entry points call on the same
    raw value. Holding one function means a write from a subdir and a read
    from the repo root cannot land in different buckets (#948); when this was
    two copies, only the CLI half existed.

    `allow_slug=False` because `--project` is a PATH. The library needs the
    slug-passthrough branch for its bucket-iterating readers; the CLI must not
    have it, or a slug-shaped `--project` would address that bucket on every
    verb, writes included, bypassing both the ten-verb limit `--slug` is held
    to and the tenant-scope refusal that guards it (#899).

    `for_write=True` (#1092) additionally refuses (`config.ProjectWriteRefused`,
    left for the caller to catch) a request that only resolved elsewhere
    because `Path.home()` is itself a git repository. See
    `config.resolve_project_dir_for_write`'s docstring for why this cannot be
    the default for every call site: it must never touch a read.
    """
    project = arg or config.project_dir() or os.getcwd()
    if for_write:
        return config.resolve_project_dir_for_write(project, allow_slug=False)
    return config.resolve_project_dir(project, allow_slug=False)


def require_ledger(project, name: str) -> None:
    """Refuse, before a verb looks a record up, when the bucket ledger `name`
    is not proven (D10.3): a ledger that cannot be read must not answer
    "unknown id". Raises `jsonl.Refused`, which `main` prints and turns into
    exit 2."""
    path = store.ledger_file(project, name)
    if path is not None:
        jsonl.require_writable(path, Writer.HUMAN)


def loops_lists_project(project: str) -> bool:
    """True when a bare `daimon loops` run from here would list `project`'s
    items: the pointer in a briefing note is only honest then. An explicit
    --project (or --slug) that names a different project than the caller's own
    resolution means the reader would be sent to the wrong listing."""
    return project == _resolve_project(None)


def _note_usage(command: str) -> None:
    """One LOCAL line per deliberate read command (#54): `<iso> <command>` to
    usage.log. Never transmitted anywhere — `daimon stats` aggregates it so a
    user can answer "do I actually re-read briefings?" (and choose to share
    the answer). Best-effort, and silent under the kill switch: disabled
    means daimon writes nothing."""
    if config.is_disabled():
        return
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "usage.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {command}\n")
    except OSError:
        pass


# #919: usage.log key prefix for the per-project receipt-probe counters
# `daimon stats` renders. usage.log is otherwise a per-MACHINE log (#54's own
# design, and #477's own lesson about conflating populations) — every other
# worldcheck counter here rides it unscoped. These four fold the project
# slug into the command name itself so `_stats_receipts` can filter to just
# THIS project, the one place worldcheck's stats need to be per-project
# rather than per-machine (the issue this constant answers: a receipt probe
# fires and leaves zero trace ANYWHERE, #919's diagnosis).
_RECEIPT_PROBE_USAGE_PREFIX = "worldcheck:receipt-probe:"


def _note_receipt_probe_usage(project_dir, wc_stats: dict) -> None:
    """Fold this run's receipt-probe telemetry into usage.log, project-scoped
    (#919). `attempted`/`eligible` come from `worldcheck.last_receipt_probe()`
    — a module-level record, NOT a key in `wc_stats`, because that dict is
    asserted by exact equality across dozens of worldcheck tests and this is
    a run-level population fact, not a per-item outcome. `confirmed`/
    `contradicted`/`skipped` are read straight off `wc_stats`'s existing
    receipt-validity:<outcome> keys (already correct, just unscoped by
    project). `skipped` matters as much as the other two: it is the SILENT
    case #919 exists to expose — a probe that fired and answered nothing (no
    CLI, no pubkey, a killed deadline, garbage output), which used to read
    identically to "0 confirmed, 0 contradicted" and therefore identically to
    "did nothing". A count of zero writes nothing — `_note_usage` in a
    zero-length range is simply never called, so a disabled or silent run
    leaves the log untouched, same as every other counter here."""
    slug = store.project_slug(project_dir)
    if not slug:
        return
    probe = worldcheck.last_receipt_probe()
    counts = {
        "attempted": probe["attempted"],
        "eligible": probe["eligible"],
        "confirmed": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:confirmed", 0),
        "contradicted": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:contradicted", 0),
        "skipped": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:skipped", 0),
    }
    for label, count in counts.items():
        for _ in range(int(count)):
            _note_usage(f"{_RECEIPT_PROBE_USAGE_PREFIX}{label}:{slug}")


# ---- recall: FTS search over local + team checkpoint history (#112) ----


def _refuses_caller_scope(slug=None, all_projects: bool = False) -> bool:
    """#899: on a tenant-scoped home a caller-chosen scope is refused out
    loud, rc 2, never narrowed in silence. One check for `recall`, `brief`
    and `why`, the three read verbs that take an address."""
    if config.tenant_scoped() and (slug or all_projects):
        print(f"error: {config.TENANT_SCOPE_REFUSAL}", file=sys.stderr)
        return True
    return False


SLUG_ROUTE_HELP = ("route to another project's bucket by its slug, written "
                   "--slug=<slug> (a slug starts with '-'); routing only, "
                   "the channel gate is unchanged (#766)")


def _slug_route(args) -> tuple:
    """(project, rc) for the ten human-only decision verbs (#766 slice 4).

    `--slug` is a ROUTING flag: `decide --all-projects` prints a command per
    foreign entry, and `--project` cannot carry it (it resolves a path, and
    the slug flattening is not invertible). The slug passes straight through
    as the project, which works because `store.project_slug` is idempotent on
    slugs, the same mechanism `brief --slug` relies on. It mints nothing and
    leaves every channel gate where it was; on a tenant-scoped home (#899)
    it is refused, since a caller choosing a bucket is the primitive that
    mode removes. rc is 0 on success, else the exit code to return."""
    slug = getattr(args, "slug", None)
    project_arg = getattr(args, "project", None)
    if slug and project_arg:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return None, 2
    if _refuses_caller_scope(slug):
        return None, 2
    return (slug or _resolve_project(project_arg)), 0


def guarded(fn):
    """Decorate a read verb that answers through the view: any exception it
    raises (a view that cannot be built is a bug, never an empty answer)
    becomes one `error:` line on stderr and exit 2, with nothing rendered
    around it. The line names the verb and the exception type, never the
    message, which can carry content. `jsonl.Refused` passes through: `main`
    already prints it and turns it into exit 2 (10b's refusal path)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except jsonl.Refused:
            raise
        # reported as one line, never shown around
        except Exception as exc:  # noqa: BLE001
            verb = fn.__name__.removeprefix("_cmd_").replace("_", " ")
            print(f"error: {verb} could not be read "
                  f"({type(exc).__name__}); nothing was shown",
                  file=sys.stderr)
            return 2

    return wrapper


# ---- resolve/log: zero-LLM append-only event writers (#102) ----


# ---- #708: the refutation/ruling/amendment verb families live in their own
# modules. EVERY moved name is re-imported here — `cli.<name>` is a stable
# seam: tests and host hooks resolve these through this module, and each
# family's register() binds handlers via this namespace so a monkeypatch on
# `cli` lands on moved code too.
from ._ledger import (  # noqa: E402
    _print_refutation,  # noqa: F401 — re-exported for compat
    _print_ruling,  # noqa: F401 — re-exported for compat
    _refutation_json,  # noqa: F401 — re-exported for compat
    _refuse_ruling_id,  # noqa: F401 — re-exported for compat
    _refute_channel,  # noqa: F401 — re-exported for compat
)
from .refute import (  # noqa: E402
    _cmd_refute_add,  # noqa: F401 — re-exported for compat
    _cmd_refute_guard,  # noqa: F401 — re-exported for compat
    _cmd_refute_list,  # noqa: F401 — re-exported for compat
    _cmd_refute_overturn,  # noqa: F401 — re-exported for compat
    _cmd_refute_ratify,  # noqa: F401 — re-exported for compat
    _cmd_refute_revise,  # noqa: F401 — re-exported for compat
    _cmd_refute_search,  # noqa: F401 — re-exported for compat
    _cmd_refute_show,  # noqa: F401 — re-exported for compat
)
from .ruling import (  # noqa: E402
    _cmd_ruling_check_try,  # noqa: F401 — re-exported for compat
    _cmd_ruling_checks,  # noqa: F401 — re-exported for compat
    _cmd_ruling_list,  # noqa: F401 — re-exported for compat
    _cmd_ruling_propose,  # noqa: F401 — re-exported for compat
    _cmd_ruling_ratify,  # noqa: F401 — re-exported for compat
    _cmd_ruling_retire,  # noqa: F401 — re-exported for compat
    _cmd_ruling_revise,  # noqa: F401 — re-exported for compat
    _cmd_ruling_show,  # noqa: F401 — re-exported for compat
)
from .amend import (  # noqa: E402
    _amend_channel,  # noqa: F401 — re-exported for compat
    _cmd_amend_list,  # noqa: F401 — re-exported for compat
    _cmd_amend_propose,  # noqa: F401 — re-exported for compat
    _cmd_amend_verdict,  # noqa: F401 — re-exported for compat
)
from .ledger_cmd import (  # noqa: E402
    _cmd_ledger_repair,  # noqa: F401 — re-exported for compat
)
from .trust import (  # noqa: E402
    _cmd_trust_list,  # noqa: F401 — re-exported for compat
    _cmd_trust_propose,  # noqa: F401 — re-exported for compat
    _cmd_trust_repair,  # noqa: F401 — re-exported for compat
    _cmd_trust_show,  # noqa: F401 — re-exported for compat
    _cmd_trust_verdict,  # noqa: F401 — re-exported for compat
    _trust_channel,  # noqa: F401 — re-exported for compat
)
from .request import (  # noqa: E402
    _cmd_request_done,  # noqa: F401 — re-exported for compat
    _cmd_request_inbox,  # noqa: F401 — re-exported for compat
    _cmd_request_inject,  # noqa: F401 — re-exported for compat
    _cmd_request_list,  # noqa: F401 — re-exported for compat
    _cmd_request_open,  # noqa: F401 — re-exported for compat
    _cmd_request_reply,  # noqa: F401 — re-exported for compat
    _cmd_request_revise,  # noqa: F401 — re-exported for compat
    _cmd_request_verdict,  # noqa: F401 — re-exported for compat
    _request_channel,  # noqa: F401 — re-exported for compat
)
from .lifecycle import (  # noqa: E402
    _cmd_forget,  # noqa: F401 — re-exported for compat
    _cmd_decide,  # noqa: F401 — re-exported for compat
    _cmd_loops,  # noqa: F401 — re-exported for compat
    _cmd_resolve,  # noqa: F401 — re-exported for compat
    _cmd_reverify,  # noqa: F401 — re-exported for compat
    _is_supersede_candidate,  # noqa: F401 — re-exported for compat
)
from .audit import (  # noqa: E402
    _audit_item_source,  # noqa: F401 — re-exported for compat
    _cmd_audit_privacy,  # noqa: F401 — re-exported for compat
    _cmd_audit_quotes,  # noqa: F401 — re-exported for compat
    _cmd_audit_quotes_deprecated,  # noqa: F401 — re-exported for compat
    _legacy_audit_source,  # noqa: F401 — re-exported for compat
    _load_audit_transcript,  # noqa: F401 — re-exported for compat
    _resolve_audit_source,  # noqa: F401 — re-exported for compat
)
from .team import (  # noqa: E402
    _cmd_team_init,  # noqa: F401 — re-exported for compat
    _cmd_team_status,  # noqa: F401 — re-exported for compat
    _cmd_team_sync,  # noqa: F401 — re-exported for compat
)
from .check import (  # noqa: E402
    _cmd_check_sync,  # noqa: F401 — re-exported for compat
)
from .history import (  # noqa: E402
    _cmd_blame,  # noqa: F401 — re-exported for compat
    _cmd_diff,  # noqa: F401 — re-exported for compat
)
from .hooks import (  # noqa: E402
    _cmd_hooks_install,  # noqa: F401 — re-exported for compat
    _cmd_hooks_list,  # noqa: F401 — re-exported for compat
    _cmd_hooks_status,  # noqa: F401 — re-exported for compat
)
from .skill import (  # noqa: E402
    _cmd_skill_install,  # noqa: F401 — re-exported for compat
    _cmd_skill_list,  # noqa: F401 — re-exported for compat
    _cmd_skill_show,  # noqa: F401 — re-exported for compat
    _cmd_skill_uninstall,  # noqa: F401 — re-exported for compat
    _resolve_project_cwd,  # noqa: F401 — re-exported for compat
)
from ._hookhosts import (  # noqa: E402
    _HOOK_HOSTS,  # noqa: F401 — re-exported for compat
    _HookHostSpec,  # noqa: F401 — re-exported for compat
    _codex_registration_status,  # noqa: F401 — re-exported for compat
    _hook_drift_present,  # noqa: F401 — re-exported for compat
    _hook_file_status,  # noqa: F401 — re-exported for compat
    _hooks_status_report,  # noqa: F401 — re-exported for compat
    _hooks_target_dir,  # noqa: F401 — re-exported for compat
    _host_install_dir,  # noqa: F401 — re-exported for compat
    _host_scripts,  # noqa: F401 — re-exported for compat
    _host_status_entry,  # noqa: F401 — re-exported for compat
    _plugin_drift,  # noqa: F401 — re-exported for compat
    _plugin_drift_present,  # noqa: F401 — re-exported for compat
    _plugin_manifest_version,  # noqa: F401 — re-exported for compat
    _registration_status,  # noqa: F401 — re-exported for compat
    _skill_drift_present,  # noqa: F401 — re-exported for compat
    _version_tuple,  # noqa: F401 — re-exported for compat
)
from .stats import (  # noqa: E402
    _RETENTION_WINDOW_DAYS,  # noqa: F401 — re-exported for compat
    _cmd_stats,  # noqa: F401 — re-exported for compat
    _earlier,  # noqa: F401 — re-exported for compat
    _stats_checks,  # noqa: F401 — re-exported for compat
    _stats_events,  # noqa: F401 — re-exported for compat
    _stats_receipts,  # noqa: F401 — re-exported for compat
    _stats_resolutions,  # noqa: F401 — re-exported for compat
    _stats_retention,  # noqa: F401 — re-exported for compat
    _stats_stitching,  # noqa: F401 — re-exported for compat
    _stats_store,  # noqa: F401 — re-exported for compat
    _stats_usage,  # noqa: F401 — re-exported for compat
    _stats_verification,  # noqa: F401 — re-exported for compat
)
from .configure_cmd import (  # noqa: E402
    _ask_backend_updates,  # noqa: F401 — re-exported for compat
    _cmd_configure,  # noqa: F401 — re-exported for compat
    _configure_flag_updates,  # noqa: F401 — re-exported for compat
    _configure_wizard,  # noqa: F401 — re-exported for compat
    _configure_wizard_flags,  # noqa: F401 — re-exported for compat
    _configure_write_flags,  # noqa: F401 — re-exported for compat
    _run_backend_test,  # noqa: F401 — re-exported for compat
)
from .status import (  # noqa: E402
    _CAPTURE_MIN_SESSIONS,  # noqa: F401 — re-exported for compat
    _capture_alarm,  # noqa: F401 — re-exported for compat
    _checkpoint_info,  # noqa: F401 — re-exported for compat
    _cmd_mcp_serve,  # noqa: F401 — re-exported for compat
    _cmd_status,  # noqa: F401 — re-exported for compat
    _cmd_verify_receipt,  # noqa: F401 — re-exported for compat
    _crash_log_info,  # noqa: F401 — re-exported for compat
    _created_epoch,  # noqa: F401 — re-exported for compat
    _print_suppressed,  # noqa: F401 — re-exported for compat
    _status_checks,  # noqa: F401 — re-exported for compat
    _status_health,  # noqa: F401 — re-exported for compat
    _status_ledgers,  # noqa: F401 — re-exported for compat
    _status_world,  # noqa: F401 — re-exported for compat
    _tail_log_info,  # noqa: F401 — re-exported for compat
    _write_worldcheck_ledger,  # noqa: F401 — re-exported for compat
    status_payload,  # noqa: F401 — re-exported for compat
)
from .inject import (  # noqa: E402
    _ACTION_BUDGET,  # noqa: F401 — re-exported for compat
    _AGE_GATE_DAYS,  # noqa: F401 — re-exported for compat
    _DEFAULT_MCP_TOOL_NAME,  # noqa: F401 — re-exported for compat
    _INJECT_BUDGET,  # noqa: F401 — re-exported for compat
    _INJECT_FETCH,  # noqa: F401 — re-exported for compat
    _LEAD_WIDTH,  # noqa: F401 — re-exported for compat
    _MCP_TOOL_NAME_RE,  # noqa: F401 — re-exported for compat
    _ORIGIN_BUDGET,  # noqa: F401 — re-exported for compat
    _SEEN_PRUNE_SECONDS,  # noqa: F401 — re-exported for compat
    _SLOT_WIDTH,  # noqa: F401 — re-exported for compat
    _STALE_MIN_HITS,  # noqa: F401 — re-exported for compat
    _UNSET,  # noqa: F401 — re-exported for compat
    _choose_recall_rows,  # noqa: F401 — re-exported for compat
    _cmd_recall_inject,  # noqa: F401 — re-exported for compat
    _fit_item_text,  # noqa: F401 — re-exported for compat
    _inject_age_bucket,  # noqa: F401 — re-exported for compat
    _load_seen,  # noqa: F401 — re-exported for compat
    _row_age_days,  # noqa: F401 — re-exported for compat
    _save_seen,  # noqa: F401 — re-exported for compat
    _save_seen_atomic,  # noqa: F401 — re-exported for compat
    _seen_path,  # noqa: F401 — re-exported for compat
    _suggest_line,  # noqa: F401 — re-exported for compat
    age_gate_blocks,  # noqa: F401 — re-exported for compat
    cooled_origins,  # noqa: F401 — re-exported for compat
)
from .action_recall import (  # noqa: E402
    _ACTION_VERBS,  # noqa: F401 — re-exported for compat
    _ACTION_VERB_PAIRS,  # noqa: F401 — re-exported for compat
    _HEREDOC_MARK,  # noqa: F401 — re-exported for compat
    _action_query_text,  # noqa: F401 — re-exported for compat
    _action_verb,  # noqa: F401 — re-exported for compat
    _cmd_action_recall,  # noqa: F401 — re-exported for compat
    _is_env_assignment,  # noqa: F401 — re-exported for compat
)
from .handoff import (  # noqa: E402
    _HANDOFF_MAX_CHARS,  # noqa: F401 — re-exported for compat
    _cmd_handoff,  # noqa: F401 — re-exported for compat
    _cmd_log,  # noqa: F401 — re-exported for compat
)
from .relations_cmd import (  # noqa: E402
    _cmd_relations_list,  # noqa: F401 — re-exported for compat
    _cmd_relations_show,  # noqa: F401 — re-exported for compat
    _cmd_relations_verdict,  # noqa: F401 — re-exported for compat
    _relations_channel,  # noqa: F401 — re-exported for compat
)
from .projects import (  # noqa: E402
    _TOPIC_TEASER_CHARS,  # noqa: F401 — re-exported for compat
    _bucket_migrate_lines,  # noqa: F401 — re-exported for compat
    _cmd_bucket_migrate,  # noqa: F401 — re-exported for compat
    _cmd_projects,  # noqa: F401 — re-exported for compat
    _cmd_slug,  # noqa: F401 — re-exported for compat
    _migrate_command,  # noqa: F401 — re-exported for compat
    _raw_project,  # noqa: F401 — re-exported for compat
    _topic_teaser,  # noqa: F401 — re-exported for compat
    projects_listing,  # noqa: F401 — re-exported for compat
    projects_rows,  # noqa: F401 — re-exported for compat
)
from .search import (  # noqa: E402
    _cmd_recall,  # noqa: F401 — re-exported for compat
    _cmd_serve,  # noqa: F401 — re-exported for compat
    _cmd_why,  # noqa: F401 — re-exported for compat
)
from .brief import (  # noqa: E402
    _cmd_anchor,  # noqa: F401 — re-exported for compat
    _cmd_brief,  # noqa: F401 — re-exported for compat
    _render_briefing_body,  # noqa: F401 — re-exported for compat
    _team_briefings,  # noqa: F401 — re-exported for compat
)
from .serialize import (  # noqa: E402
    _attach_serialize_log_handler,  # noqa: F401 — re-exported for compat
    _cmd_heal,  # noqa: F401 — re-exported for compat
    _cmd_serialize,  # noqa: F401 — re-exported for compat
    _cmd_write_checkpoint,  # noqa: F401 — re-exported for compat
    _emit_supersede_candidates,  # noqa: F401 — re-exported for compat
    _preflight_error,  # noqa: F401 — re-exported for compat
    _print_error,  # noqa: F401 — re-exported for compat
    _run_serialize,  # noqa: F401 — re-exported for compat
    _session_end_stamp,  # noqa: F401 — re-exported for compat
)
from . import (  # noqa: E402
    action_recall,
    amend,
    audit,
    brief,
    check,
    configure_cmd,
    handoff,
    history,
    hooks,
    inject,
    ledger_cmd,
    lifecycle,
    projects,
    refute,
    relations_cmd,
    request,
    ruling,
    search,
    serialize,
    skill,
    stats,
    status,
    team,
    trust,
)


def _crash_stamp_excepthook(exc_type, exc, tb) -> None:
    """Uncaught-crash header (#92): serialize-crash.log is the detached
    child's RAW stderr fd — no logger sits in the write path, so the only
    process that can timestamp a crash is the crashing one. One ISO-stamped
    line, then the traceback. Covers uncaught Python exceptions (the
    dominant case); interpreter-level deaths still write nothing.

    #605: the traceback is formatted HERE rather than handed to
    sys.__excepthook__, so it can pass through redact_text on the way out.
    The crashing process is the only one that can scrub these bytes — for
    the same reason it is the only one that can stamp them — and #513
    redacted the tail on READ over a file nothing deleted. Item text still
    survives (redaction catches secrets, not beliefs), which is why the
    purge above it is wholesale.

    Fail-open, redact.py's own posture: anything that goes wrong formatting
    or redacting falls back to the stock hook, because a swallowed traceback
    is a crash nobody can diagnose."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), "?")
    print(f"--- crash {stamp} pid={os.getpid()} cmd={cmd} ---",
          file=sys.stderr, flush=True)
    try:
        formatted = "".join(traceback.format_exception(exc_type, exc, tb))
        redacted, _ = redact.redact_text(formatted)
        print(redacted, end="", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — see fail-open above
        sys.__excepthook__(exc_type, exc, tb)


def build_parser() -> argparse.ArgumentParser:
    """The full daimon parser tree, extracted from main (#431) so tests can
    walk the subparser registry mechanically — the write-audit architecture
    guard enumerates every command argparse knows about, so a NEW subcommand
    is enumerated (and audited) automatically the moment it is registered."""
    # #68: one formatter selection for the WHOLE parser tree. argparse does not
    # propagate formatter_class from parent to subparser, so every add_parser
    # call below must receive it — done here by patching add_parser on each
    # subparsers action into a partial pre-bound with `fmt`, rather than
    # threading formatter_class= through 20+ individual call sites.
    fmt = _formatter_class()
    parser = argparse.ArgumentParser(
        prog="daimon",
        description="Cognitive checkpoints — serialize sessions, brief on resume.",
        epilog="Examples:\n"
               "  daimon brief                 render the latest briefing\n"
               "  daimon status                checkpoint presence + last serialize\n"
               "  daimon configure             detect/repair the LLM backend\n"
               "\n"
               "Docs:   https://daily-nerd.github.io/daimon/\n"
               "Issues: https://github.com/Daily-Nerd/daimon/issues\n",
        formatter_class=fmt,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    # Rebinding the bound method is the point: every subparser below then
    # gets `fmt` without repeating it at ~90 call sites.
    sub.add_parser = functools.partial(  # type: ignore[method-assign]
        sub.add_parser, formatter_class=fmt)

    serialize.register(sub, fmt)


    brief.register(sub, fmt)


    search.register(sub, fmt)


    history.register(sub, fmt)

    search.register_serve(sub, fmt)


    projects.register(sub, fmt)


    lifecycle.register(sub, fmt)

    refute.register(sub, fmt)

    ruling.register(sub, fmt)

    amend.register(sub, fmt)

    trust.register(sub, fmt)

    ledger_cmd.register(sub, fmt)

    request.register(sub, fmt)

    relations_cmd.register(sub, fmt)


    handoff.register(sub, fmt)


    inject.register(sub, fmt)


    action_recall.register(sub, fmt)


    # #756: the second UserPromptSubmit backend, top-level beside
    # `recall-inject` rather than under `request` — it is a hook backend, not
    # one of the request object's verbs, and the command-catalogue guard
    # (#650) only partitions the TOP-LEVEL surface, so a subcommand here
    # would reach no skill and trip no test.
    p_rq_inject = sub.add_parser(
        "request-inject",
        help="live-delivery backend for the UserPromptSubmit hook (#756): "
             "prints undecided asks this session has not been shown, rc 0 always",
    )
    p_rq_inject.add_argument("--project", default=None,
                             help="project dir for scoping (defaults to cwd detection)")
    p_rq_inject.add_argument("--session", default=None,
                             help="current session id (half the delivery write-once key)")
    p_rq_inject.set_defaults(func=_cmd_request_inject)

    status.register(sub, fmt)


    audit.register(sub, fmt)

    serialize.register_heal(sub, fmt)


    team.register(sub, fmt)

    configure_cmd.register(sub, fmt)


    stats.register(sub, fmt)


    check.register(sub, fmt)

    hooks.register(sub, fmt)

    skill.register(sub, fmt)

    status.register_mcp(sub, fmt)

    return parser


def main(argv=None) -> int:
    sys.excepthook = _crash_stamp_excepthook  # #92: stamp uncaught crashes
    parser = build_parser()

    # Slugs are munged absolute paths, so they START with "-" ("/Users/x" ->
    # "-Users-x") — argparse reads `--slug -Users-x` as a missing argument and
    # only accepts the `=` form. Fuse the pair pre-parse so both spellings
    # work; a trailing bare `--slug` is left for argparse to reject normally.
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    for i, tok in enumerate(argv[:-1]):
        if tok == "--slug":
            argv[i:i + 2] = [f"--slug={argv[i + 1]}"]
            break

    # #691: `daimon amend <item-id> …` is the documented propose spelling;
    # argparse subcommands need the verb word, so fuse it pre-parse. Only an
    # item-id-shaped second token is rewritten — verbs and ids cannot collide
    # (no verb matches the id shape).
    if (len(argv) > 1 and argv[0] == "amend"
            and amendments._ITEM_ID_RE.fullmatch(argv[1])):
        argv.insert(1, "propose")

    args = parser.parse_args(argv)
    # D10.3: the CLI is the human channel, so a refused write is surfaced
    # here (one handler for every verb) instead of being swallowed into the
    # appender's "not written". A library caller never goes through here.
    try:
        with jsonl.surface_refusals():
            return args.func(args)
    except jsonl.Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
