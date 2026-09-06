"""`daimon check` verbs — the armed-check manifest (#943).

One verb so far. `check sync` rebuilds `~/.daimon/checks/` from the ledger:
the manifest a host hook reads and one executable body per armed check.

Every ledger writer already calls the same function, so this exists for the
cases a writer cannot cover — a manifest deleted or corrupted by hand, an
install whose checks directory moved, a sync that failed and printed its
warning. Safe to repeat, and `hooks install` will call it in slice 3.
"""

import daimon_briefing.cli as _cli

from .. import checks, render


def _cmd_check_sync(args) -> int:
    project = _cli._resolve_project(args.project)
    report = checks.sync(project)
    if not report.ok:
        # stdout, like every other refusal in the ledger families: #194
        # moved diagnostics off stderr, and the warning the ruling verbs
        # print for the same failure goes here too.
        print(f"checks: manifest not updated ({report.reason})")
        return 1
    # Reported even at zero. A silent success is how a project that armed
    # nothing and a project whose sync never ran come to look identical.
    render.render_ledger_lines(
        [f"checks: {report.armed} armed for {report.slug}"])
    return 0


def register(sub, fmt) -> None:
    """Register the `check` parser family on the top-level subparsers."""
    p_check = sub.add_parser(
        "check",
        help="armed rulings' checks (#943): rebuild what a host hook reads")
    check_sub = p_check.add_subparsers(dest="check_cmd", required=True)
    pc_sync = check_sub.add_parser(
        "sync",
        help="rebuild the check manifest and bodies from this project's "
             "ledger; safe to repeat, and every ratify already does it")
    pc_sync.add_argument(
        "--project",
        help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    # `--slug` is deliberately absent. Slice 2 adds no routing primitive:
    # that flag reaches ten human-only decision verbs and is refused outright
    # on a tenant-scoped home, and widening it to a verb that materializes
    # executables would hand bucket-choosing power past the check that gates
    # it (#899, #948).
    pc_sync.set_defaults(func=_cli._cmd_check_sync)
