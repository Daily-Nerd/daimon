"""`daimon ruling` verbs — standing rulings, the ledger's positive polarity
(#693).

Pure move out of the cli monolith (#708). Shared helpers that remain in the
package `__init__` are reached through the module object (`_cli.<name>`) so
the `cli.<name>` seam tests and hosts patch keeps working on moved code.
"""

import argparse
import sys

import daimon_briefing.cli as _cli

from .. import config, normalize, refutations, render
from ._ledger import (
    _print_ruling,
    _refutation_json,
    _report_vanished_write,
    _refute_channel,
    _ruling_lines,
)


def _cmd_ruling_propose(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        ruling_id = refutations.assert_ruling(
            subject=args.subject, verdict=args.verdict, scope=args.scope,
            evidence=args.evidence, channel=_refute_channel(args),
            anchors=args.anchor,
            revisit_when=args.revisit_when or "", ratified=args.ratify,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not recorded: {exc}")
        return 1
    record = refutations.get(ruling_id, project_dir=project)
    _cli._note_usage("ruling:propose")
    if record is None:
        _report_vanished_write(ruling_id, "propose", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record["state"] == "candidate":
            if getattr(args, "by", None) != "agent":
                render.render_ledger_lines(
                    [f"  Next: daimon ruling ratify {ruling_id} "
                     f"--project {project}"])
            else:
                render.render_ledger_lines(
                    ["  Candidate recorded. Activation requires an explicit "
                     "human decision."])
    return 0


def _cmd_ruling_ratify(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(f"ruling not ratified: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute ratify`")
        return 1
    if channel != "cli-tty":
        # Never invoke a writer to harvest its error string.
        print("ruling not ratified: ratification requires a human channel; "
              f"this call arrived through {channel!r}")
        return 1
    # Ratification is a signature, not an id-typing exercise: print the FULL
    # text, disclose the render consequence, and bind the append to the key
    # of the text DISPLAYED — the fold refuses to activate any other text.
    # Under --json the ceremony goes to stderr so stdout stays parseable.
    ceremony = sys.stderr if args.json else sys.stdout
    print("About to ratify this ruling:", file=ceremony)
    if not args.json:
        _print_ruling(record, detailed=True)
    else:
        print(f"  {record.get('verdict', '')}", file=ceremony)
    print("  This text will render into every future session for this "
          "project.", file=ceremony)
    displayed_key = normalize.content_key(record.get("verdict") or "")
    answer = input("Ratify? [y/N]: ").strip().casefold()
    if answer not in ("y", "yes"):
        print("not ratified")
        return 1
    try:
        refutations.ratify(args.ruling_id, channel=channel,
                           note=args.note or "", verdict_key=displayed_key,
                           project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not ratified: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:ratify")
    if record is None:
        _report_vanished_write(args.ruling_id, "ratify", as_json=args.json)
        return 0
    if record["state"] != "active":
        print("not activated: the text changed during confirmation; "
              "re-run to review the current text")
        return 1
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
    return 0


def _cmd_ruling_revise(args) -> int:
    project = _cli._resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute revise`")
        return 1
    if args.ratify:
        # The ratification ceremony (full text, disclosure, confirm, key
        # binding) lives in ONE place; a revise flag walking around it would
        # activate text the human was never shown.
        print("ruling revise does not activate; revise the candidate, then "
              f"run `daimon ruling ratify {args.ruling_id}`")
        return 1
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(f"ruling not revised: {exc}")
        return 1
    if (record["state"] == "active" and channel == "cli-tty"
            and (args.verdict is not None or args.subject is not None)):
        # Rewriting what renders is the same power ratification has, and it
        # earns trust the same way: show the change, disclose, confirm.
        print("About to change the ACTIVE text of this ruling:")
        _print_ruling(record, detailed=True)
        if args.verdict is not None:
            print(f"  New text: {args.verdict}")
        if args.subject is not None:
            print(f"  New governs: {args.subject}")
        print("  This text will render into every future session for this "
              "project.")
        answer = input("Apply? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("not revised")
            return 1
    anchors = args.anchor if args.anchor is not None else None
    try:
        refutations.revise(
            args.ruling_id, channel=channel,
            evidence=args.evidence, subject=args.subject,
            verdict=args.verdict, scope=args.scope,
            anchors=anchors, revisit_when=args.revisit_when,
            ratified=False, project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not revised: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:revise")
    if record is None:
        _report_vanished_write(args.ruling_id, "revise", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record.get("revision_proposed"):
            render.render_ledger_lines(
                ["  Proposal recorded; the active ruling is untouched "
                 "until a human verdict."])
        elif record["state"] == "candidate":
            render.render_ledger_lines(
                ["  Revision is not load-bearing until explicit human "
                 "ratification."])
    return 0


def _cmd_ruling_retire(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    try:
        event = refutations.retire(
            args.ruling_id, channel=_refute_channel(args),
            evidence=args.evidence or (),
            note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not retired: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:retire")
    if record is None:
        _report_vanished_write(args.ruling_id, "retire", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if event == "overturn-proposed":
            render.render_ledger_lines(
                ["  Retirement proposed; the ruling stands until a human "
                 "verdict."])
    return 0


def _cmd_ruling_list(args) -> int:
    project = _cli._resolve_project(args.project)
    rows = refutations.listing(states=set(args.state or refutations.STATES),
                               polarity="ruling", project_dir=project)
    _cli._note_usage("ruling:list")
    if args.json:
        print(_refutation_json(rows))
        active_j = sum(1 for r in rows if r.get("state") == "active")
        cap_j = config.ruling_cap()
        if active_j > cap_j:
            # stderr so the JSON stays parseable and machines still learn it.
            print(f"over cap: {active_j} active vs cap {cap_j}",
                  file=sys.stderr)
        return 0
    if not rows:
        render.render_ledger_lines(["no rulings for this project"])
        return 0
    render.render_ledger_records([_ruling_lines(row) for row in rows])
    # The cap binds ACTIVATION; a lowered DAIMON_RULING_CAP leaves the
    # excess active, so the over-cap state must be visible somewhere.
    active = sum(1 for r in rows if r.get("state") == "active")
    cap = config.ruling_cap()
    if active > cap:
        render.render_ledger_lines(
            [f"over cap: {active} active vs cap {cap} — retire one, or "
             "raise DAIMON_RULING_CAP deliberately"])
    return 0


def _cmd_ruling_show(args) -> int:
    project = _cli._resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute show`")
        return 1
    _cli._note_usage("ruling:show")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
    return 0


def register(sub, fmt) -> None:
    """Register the `ruling` parser family on the top-level subparsers."""
    p_ruling = sub.add_parser(
        "ruling",
        help="standing rulings: human-ratified records that never decay (#693)",
        epilog="Examples:\n"
               "  daimon ruling propose --subject 'public posts' "
               "--verdict 'internal numbers never appear in public posts' "
               "--scope publishing --evidence issue:693 --by agent\n"
               "  daimon ruling ratify r-1a2b3c4d5e6f\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ruling_sub = p_ruling.add_subparsers(dest="ruling_cmd", required=True)

    rl_propose = ruling_sub.add_parser(
        "propose", help="propose a standing ruling; agent proposals stay "
                        "candidates until a human ratifies")
    rl_propose.add_argument("--subject", required=True,
                            help="what the ruling governs")
    rl_propose.add_argument("--verdict", required=True,
                            help="the ruling text itself (max 280 chars)")
    rl_propose.add_argument("--scope", required=True,
                            help="where it applies")
    rl_propose.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="cited source behind the ruling; recorded verbatim, not "
             "resolved or verified; repeatable")
    rl_propose.add_argument(
        "--anchor", action="append", default=[], metavar="ANCHOR",
        help="stable exact-match key such as issue:693; repeatable")
    rl_propose.add_argument(
        "--revisit-when", help="condition that makes reconsideration legitimate")
    rl_propose.add_argument("--by", choices=["agent"], default=None,
                            help="declare yourself an agent; omit it only "
                                 "from an interactive terminal")
    rl_propose.add_argument(
        "--ratify", action="store_true",
        help="activate immediately; valid only on the human path")
    rl_propose.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_propose.add_argument("--json", action="store_true", help="machine-readable output")
    rl_propose.set_defaults(func=_cli._cmd_ruling_propose)

    rl_ratify = ruling_sub.add_parser(
        "ratify", help="activate a candidate ruling; prints the full text "
                       "and confirms before the append")
    rl_ratify.add_argument("ruling_id", help="exact r-… id")
    rl_ratify.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; ratification "
                                "then refuses")
    rl_ratify.add_argument("--note", help="optional ratification rationale")
    rl_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_ratify.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    rl_ratify.add_argument("--json", action="store_true", help="machine-readable output")
    rl_ratify.set_defaults(func=_cli._cmd_ruling_ratify)

    rl_revise = ruling_sub.add_parser(
        "revise", help="revise a ruling; on an active ruling an agent call "
                       "records a proposal and the text stands")
    rl_revise.add_argument("ruling_id", help="exact r-… id")
    rl_revise.add_argument("--subject", help="replacement subject")
    rl_revise.add_argument("--verdict", help="replacement ruling text")
    rl_revise.add_argument("--scope", help="replacement scope")
    rl_revise.add_argument(
        "--anchor", action="append", default=None, metavar="ANCHOR",
        help="replacement anchor set; repeatable")
    rl_revise.add_argument("--revisit-when", help="replacement revisit condition")
    rl_revise.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="source cited for the revision; recorded, not verified; repeatable")
    rl_revise.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; on an active ruling the call then "
             "records a proposal and the text stands")
    rl_revise.add_argument(
        "--ratify", action="store_true",
        help="refused: activation goes through `daimon ruling ratify`, "
             "which shows the text before the write")
    rl_revise.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_revise.add_argument("--json", action="store_true", help="machine-readable output")
    rl_revise.set_defaults(func=_cli._cmd_ruling_revise)

    rl_retire = ruling_sub.add_parser(
        "retire", help="end a ruling; agent calls propose, humans retire "
                       "directly; evidence optional")
    rl_retire.add_argument("ruling_id", help="exact r-… id")
    rl_retire.add_argument(
        "--evidence", action="append", default=None, metavar="SOURCE",
        help="optional source for why the ruling stopped applying; repeatable")
    rl_retire.add_argument("--note", help="optional explanation")
    rl_retire.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; the call then records a retirement "
             "proposal and the ruling stands until a human verdict")
    rl_retire.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_retire.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    rl_retire.add_argument("--json", action="store_true", help="machine-readable output")
    rl_retire.set_defaults(func=_cli._cmd_ruling_retire)

    rl_list = ruling_sub.add_parser("list", help="list project rulings")
    rl_list.add_argument("--state", action="append",
                         choices=sorted(refutations.STATES),
                         help="filter by state; repeatable")
    rl_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_list.add_argument("--json", action="store_true", help="machine-readable output")
    rl_list.set_defaults(func=_cli._cmd_ruling_list)

    rl_show = ruling_sub.add_parser("show", help="show one ruling, including "
                                                 "pending proposals")
    rl_show.add_argument("ruling_id", help="exact r-… id")
    rl_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_show.add_argument("--json", action="store_true", help="machine-readable output")
    rl_show.set_defaults(func=_cli._cmd_ruling_show)
