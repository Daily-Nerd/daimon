"""`daimon check` verbs — the armed-check manifest (#943).

One verb so far. `check sync` rebuilds `~/.daimon/checks/` from the ledger:
the manifest a host hook reads and one executable body per armed check.

Every ledger writer already calls the same function, so this exists for the
cases a writer cannot cover — a manifest deleted or corrupted by hand, an
install whose checks directory moved, a sync that failed and printed its
warning. Safe to repeat, and `hooks install` will call it in slice 3.
"""

import json

import daimon_briefing.cli as _cli

from .. import checks, render


def audit_lines(audit, scope: str = "") -> list:
    """The manifest audit as text, shared by `check sync --check` and the
    `hooks status` trailing block so the two cannot word the same fact
    differently.

    `scope` qualifies the label where the surrounding command is not already
    project-scoped: `hooks status` audits every host on the machine, and an
    unqualified manifest line there would read as machine-wide."""
    if audit.state == "unreadable":
        return [f"checks manifest{scope}: could not be read",
                "  daimon wrote something it can no longer parse, so nothing "
                "here says what is armed",
                "  fix: daimon check sync"]
    if audit.state == "absent" and not audit.drift:
        # `render._checks_manifest_header` keeps four states apart and this
        # kept three, so a machine with nothing armed anywhere read "in
        # step, 0 armed" here and "no manifest" on the table. "In step" with
        # no file on disk is the half that misleads. `absent` is about the
        # FILE: a manifest that exists and correctly holds nothing for this
        # project is in step, and still says so.
        return [f"checks manifest{scope}: no manifest"]
    if not audit.drift:
        return [f"checks manifest{scope}: in step, {len(audit.have)} armed"]
    lines = [f"checks manifest{scope}: drifted"]
    for label, ids in (("missing from the manifest", audit.missing),
                       ("no longer wanted", audit.stale),
                       ("body missing", audit.body_missing),
                       ("body does not match its pinned hash",
                        audit.body_mismatch)):
        if ids:
            lines.append(f"  {label}: {', '.join(ids)}")
    lines.append("  fix: daimon check sync")
    return lines


def _cmd_check_audit(args) -> int:
    """#943 slice 5: grade the manifest, repair nothing.

    Three exit codes, not two. `drifted` is a repair the fix line names;
    a manifest daimon cannot parse is a state where the audit has no
    opinion about what is armed at all, and a script has to be able to tell
    those apart. `scripts/sync_hooks.py --check` uses the same 0/1 split for
    the same reason."""
    audit = checks.audit(_cli._resolve_project(args.project))
    if getattr(args, "json", False):
        print(json.dumps(audit._asdict(), indent=2))
    else:
        render.render_ledger_lines(audit_lines(audit))
    if audit.state == "unreadable":
        return 3
    return 1 if audit.drift else 0


def _cmd_check_sync(args) -> int:
    if getattr(args, "check", False):
        return _cmd_check_audit(args)
    project = _cli._resolve_project(args.project)
    report = checks.sync(project)
    if getattr(args, "json", False):
        print(json.dumps(report._asdict(), indent=2))
        return 0 if report.ok else 1
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
        "--check", action="store_true",
        help="audit only: report whether the manifest matches this project's "
             "ledger and write nothing (0 in step, 1 drifted, 3 unreadable)")
    pc_sync.add_argument(
        "--json", action="store_true", help="machine-readable output")
    pc_sync.add_argument(
        "--project",
        help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    # `--slug` is deliberately absent. Slice 2 adds no routing primitive:
    # that flag reaches ten human-only decision verbs and is refused outright
    # on a tenant-scoped home, and widening it to a verb that materializes
    # executables would hand bucket-choosing power past the check that gates
    # it (#899, #948).
    pc_sync.set_defaults(func=_cli._cmd_check_sync)
