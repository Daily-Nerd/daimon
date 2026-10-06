"""`daimon ledger` verbs: look after a bucket ledger file (#1132 2c-2).

`repair` is the one verb. It heals what `status` reports as torn, split or
garbage in a bucket ledger and re-runs forget over the result. `daimon trust
repair` is the same verb for the trust ledger.
"""

import daimon_briefing.cli as _cli

from .. import ledger_repair, render


def _report_lines(report: ledger_repair.Report) -> list:
    dry = report.dry_run
    rejoin, quarantine, rescrub = (
        ("would rejoin", "would quarantine", "would re-scrub") if dry
        else ("rejoined", "quarantined", "re-scrubbed"))
    name = report.name
    if report.outcome == "nothing" and not report.scrub_skipped:
        return [f"nothing to repair: {name} is ok"]
    lines = []
    if report.rejoined:
        lines.append(f"{rejoin} {report.rejoined} split row(s) in {name}")
    if report.torn or report.garbage:
        lines.append(
            f"{quarantine} {report.torn} torn + {report.garbage} garbage "
            f"line(s) to {report.sidecar}")
        if report.sidecar_held:
            lines.append(
                f"  {report.sidecar_held} line(s) already there; unrelated "
                "fragments are kept in that file")
    if report.keys:
        lines.append(
            f"{rescrub} {report.keys} forgotten key(s): "
            f"{report.rows} row(s) removed or redacted")
    if report.scrub_skipped:
        lines.append(f"re-scrub skipped: {report.scrub_skipped}")
    if report.dry_run:
        lines.append("dry run: nothing written")
    return lines


def _cmd_ledger_repair(args) -> int:
    ledger = ledger_repair.resolve_name(args.name)
    if ledger is None:
        print(f"unknown ledger {args.name!r}; declared: "
              + ", ".join(n.removesuffix(".jsonl")
                          for n in ledger_repair.declared_names()))
        return 2
    project = _cli._resolve_project(args.project)
    report = ledger_repair.repair(project, ledger, dry_run=args.dry_run)
    if report.outcome == "error":
        print(f"cannot repair {ledger}: {report.error}")
        return 1
    render.render_ledger_lines(_report_lines(report))
    return 1 if report.scrub_skipped else 0


def add_repair_arguments(parser) -> None:
    parser.add_argument("--project", help="project directory (default: "
                        "DAIMON_PROJECT_DIR, then cwd)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")


def register(sub, fmt) -> None:
    """Register the `ledger` parser family on the top-level subparsers."""
    p_ledger = sub.add_parser(
        "ledger",
        help="look after a bucket ledger file (#1132)",
        epilog="Examples:\n"
               "  daimon ledger repair trust --dry-run\n"
               "  daimon ledger repair events\n",
    )
    ledger_sub = p_ledger.add_subparsers(dest="ledger_cmd", required=True)
    pl_repair = ledger_sub.add_parser(
        "repair", formatter_class=fmt,
        help="rejoin split rows, move torn and garbage lines to "
             "<name>.quarantined-lines, and re-run forget over the ledger")
    pl_repair.add_argument(
        "name", help="a bucket ledger: "
        + ", ".join(n.removesuffix(".jsonl")
                    for n in ledger_repair.declared_names()))
    add_repair_arguments(pl_repair)
    pl_repair.set_defaults(func=_cli._cmd_ledger_repair)
