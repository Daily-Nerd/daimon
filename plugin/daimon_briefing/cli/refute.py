"""`daimon refute` verbs — evidence-cited negative knowledge (#573).

Pure move out of the cli monolith (#708). Shared helpers that remain in the
package `__init__` are reached through the module object (`_cli.<name>`) so
the `cli.<name>` seam tests and hosts patch keeps working on moved code.
"""

import functools

import daimon_briefing.cli as _cli

from .. import refutations, render
from ._ledger import (
    _print_refutation,
    _refusal_message,
    _refutation_json,
    _report_vanished_write,
    _refutation_lines,
    _refuse_ruling_id,
    _refute_channel,
    _ruling_lines,
)


def _cmd_refute_add(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        ref_id = refutations.assert_refutation(
            subject=args.subject, verdict=args.verdict, scope=args.scope,
            evidence=args.evidence, channel=_refute_channel(args),
            anchors=args.anchor,
            revisit_when=args.revisit_when or "", ratified=args.ratify,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation not recorded", exc))
        return 1
    record = refutations.get(ref_id, project_dir=project)
    _cli._note_usage("refute:add")
    if record is None:
        _report_vanished_write(ref_id, "add", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if record["state"] == "candidate":
            # An agent-authored candidate never gets handed its own escalation
            # command; activation is a decision the human has to reach.
            if getattr(args, "by", None) != "agent":
                render.render_ledger_lines(
                    [f"  Next: daimon refute ratify {ref_id} "
                     f"--project {project}"])
            else:
                render.render_ledger_lines(
                    ["  Candidate recorded. Activation requires an explicit "
                     "human decision."])
    return 0


def _cmd_refute_ratify(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "ratify"):
        return 1
    try:
        refutations.ratify(args.refutation_id, channel=_refute_channel(args),
                           note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation not ratified", exc))
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _cli._note_usage("refute:ratify")
    if record is None:
        _report_vanished_write(args.refutation_id, "ratify", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
    return 0


def _cmd_refute_revise(args) -> int:
    project = _cli._resolve_project(args.project)
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "revise"):
        return 1
    anchors = args.anchor if args.anchor is not None else None
    try:
        refutations.revise(
            args.refutation_id, channel=_refute_channel(args),
            evidence=args.evidence, subject=args.subject, verdict=args.verdict, scope=args.scope,
            anchors=anchors, revisit_when=args.revisit_when,
            ratified=args.ratify, project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation not revised", exc))
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _cli._note_usage("refute:revise")
    if record is None:
        _report_vanished_write(args.refutation_id, "revise", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if record["state"] == "candidate":
            render.render_ledger_lines(
                ["  Revision is not load-bearing until explicit human ratification."])
    return 0


def _cmd_refute_overturn(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "retire"):
        return 1
    try:
        event = refutations.overturn(
            args.refutation_id, channel=_refute_channel(args),
            evidence=args.evidence,
            note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation not overturned", exc))
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _cli._note_usage("refute:overturn")
    if record is None:
        _report_vanished_write(args.refutation_id, "overturn", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if event == "overturn-proposed":
            render.render_ledger_lines(
                ["  Agent evidence recorded; the active guard remains until human ratification."])
    return 0


def _cmd_refute_show(args) -> int:
    project = _cli._resolve_project(args.project)
    record = refutations.get(args.refutation_id, project_dir=project)
    if record is None:
        print(f"unknown refutation: {args.refutation_id}")
        return 1
    if _refuse_ruling_id(record, "show"):
        return 1
    _cli._note_usage("refute:show")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
    return 0


def _cmd_refute_list(args) -> int:
    project = _cli._resolve_project(args.project)
    rows = refutations.listing(states=set(args.state or refutations.STATES),
                               polarity="refutation", project_dir=project)
    _cli._note_usage("refute:list")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        render.render_ledger_lines(["no refutations for this project"])
    else:
        render.render_ledger_records([_refutation_lines(row) for row in rows])
    return 0


def _cmd_refute_search(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        rows = refutations.search(
            " ".join(args.query), project_dir=project,
            states=set(args.state or refutations.STATES))
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation search refused", exc))
        return 1
    _cli._note_usage("refute:search")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        render.render_ledger_lines(["no matching refutations"])
    else:
        # #693: search is the topic-addressable pull path, so it returns BOTH
        # polarities, labelled by their own printers — filtering rulings out
        # would leave the records that matter most with no pull surface
        # mid-session, after the briefing has scrolled away.
        render.render_ledger_records([
            _ruling_lines(row, tag=True)
            if row.get("polarity") == "ruling"
            else _refutation_lines(row, tag=True)
            for row in rows
        ])
    return 0


def _cmd_refute_guard(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        rows = refutations.guard(
            " ".join(args.query), anchors=args.anchor,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("refutation guard refused", exc))
        return 1
    # #581: split by outcome and rail, the way `resolve` splits its tags. One
    # aggregate count cannot separate a hit from a miss, so the false-veto rate
    # the design named as its own expansion gate is uncomputable from field
    # data. The rails also differ in precision, so the hit tag names which one
    # fired: an aggregate cannot say which rail generates the noise.
    _cli._note_usage(f"refute:guard:hit:{rows[0]['guard_match']['rail']}"
                     if rows else "refute:guard:miss")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        if not args.quiet:
            render.render_ledger_lines(
                ["no active refutation matched exact anchors or subject"])
    else:
        # One warning is the v1 attention budget. JSON retains every exact hit
        # for deliberation integrations that can reconcile them in batch.
        _print_refutation(rows[0], detailed=True)
        if len(rows) > 1:
            render.render_ledger_lines(
                [f"  + {len(rows) - 1} more exact match(es); use --json to inspect"])
    return 0


def register(sub, fmt) -> None:
    """Register the `refute` parser family on the top-level subparsers."""
    p_refute = sub.add_parser(
        "refute",
        help="author and query evidence-cited negative knowledge (#573)",
        epilog="Examples:\n"
               "  daimon refute list\n"
               "  daimon refute search receipt verification\n"
               "  daimon refute guard 'should we revisit #502?'\n",
    )
    refute_sub = p_refute.add_subparsers(dest="refute_cmd", required=True)
    refute_sub.add_parser = functools.partial(
        refute_sub.add_parser, formatter_class=fmt)

    pr_add = refute_sub.add_parser(
        "add", help="assert a scoped refutation; agent assertions stay candidates",
        epilog="Example:\n"
               "  daimon refute add --subject 'original #502 receipt design' "
               "--verdict 'whole-file hashes do not prove span claims' "
               "--scope 'carried-item receipt tiers' --anchor issue:502 "
               "--evidence 'measurement:566/623 origin misses' "
               "--ratify\n",
    )
    pr_add.add_argument("--subject", required=True, help="approach or claim rejected")
    pr_add.add_argument("--verdict", required=True, help="what no longer holds")
    pr_add.add_argument("--scope", required=True, help="where the verdict applies")
    pr_add.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="cited measurement, artifact, issue, or transcript source; recorded verbatim, not resolved or verified; repeatable")
    pr_add.add_argument(
        "--anchor", action="append", default=[], metavar="ANCHOR",
        help="stable exact-match key such as issue:502 or command:daimon-why; repeatable")
    pr_add.add_argument(
        "--revisit-when", help="condition that makes reconsideration legitimate")
    pr_add.add_argument("--by", choices=["agent"], default=None,
                        help="declare yourself an agent; omit it only from an "
                             "interactive terminal, which is the human path")
    pr_add.add_argument(
        "--ratify", action="store_true",
        help="activate immediately; valid only on the human path, which is "
             "an interactive terminal with no --by.")
    pr_add.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_add.add_argument("--json", action="store_true", help="machine-readable output")
    pr_add.set_defaults(func=_cli._cmd_refute_add)

    pr_ratify = refute_sub.add_parser(
        "ratify", help="explicitly activate a candidate as a human decision")
    pr_ratify.add_argument("refutation_id", help="exact r-… id")
    pr_ratify.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; ratification then refuses, because "
             "activation requires a human channel")
    pr_ratify.add_argument("--note", help="optional ratification rationale")
    pr_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_ratify.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    pr_ratify.add_argument("--json", action="store_true", help="machine-readable output")
    pr_ratify.set_defaults(func=_cli._cmd_refute_ratify)

    pr_revise = refute_sub.add_parser(
        "revise", help="append a new evidence-cited version; inactive until ratified")
    pr_revise.add_argument("refutation_id", help="exact r-… id")
    pr_revise.add_argument("--subject", help="replacement subject")
    pr_revise.add_argument("--verdict", help="replacement verdict")
    pr_revise.add_argument("--scope", help="replacement scope")
    pr_revise.add_argument(
        "--anchor", action="append", default=None, metavar="ANCHOR",
        help="replacement anchor set; repeatable")
    pr_revise.add_argument("--revisit-when", help="replacement revisit condition")
    pr_revise.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="new evidence cited for the revision; recorded, not verified; repeatable")
    pr_revise.add_argument("--by", choices=["agent"], default=None)
    pr_revise.add_argument(
        "--ratify", action="store_true",
        help="activate the revision immediately; valid only on the human "
             "path, which is an interactive terminal with no --by.")
    pr_revise.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_revise.add_argument("--json", action="store_true", help="machine-readable output")
    pr_revise.set_defaults(func=_cli._cmd_refute_revise)

    pr_overturn = refute_sub.add_parser(
        "overturn",
        help="cite evidence against the verdict; agent calls propose, humans deactivate")
    pr_overturn.add_argument("refutation_id", help="exact r-… id")
    pr_overturn.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="evidence cited against the active verdict; recorded, not "
             "verified; repeatable")
    pr_overturn.add_argument("--note", help="optional explanation")
    pr_overturn.add_argument("--by", choices=["agent"], default=None)
    pr_overturn.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_overturn.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    pr_overturn.add_argument("--json", action="store_true", help="machine-readable output")
    pr_overturn.set_defaults(func=_cli._cmd_refute_overturn)

    pr_show = refute_sub.add_parser("show", help="show one refutation and its trust signals")
    pr_show.add_argument("refutation_id", help="exact r-… id")
    pr_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_show.add_argument("--json", action="store_true", help="machine-readable output")
    pr_show.set_defaults(func=_cli._cmd_refute_show)

    pr_list = refute_sub.add_parser("list", help="list project refutations")
    pr_list.add_argument("--state", action="append", choices=sorted(refutations.STATES),
                         help="filter by state; repeatable")
    pr_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_list.add_argument("--json", action="store_true", help="machine-readable output")
    pr_list.set_defaults(func=_cli._cmd_refute_list)

    pr_search = refute_sub.add_parser(
        "search", help="search the complete ledger without age decay")
    pr_search.add_argument("query", nargs="+", help="subject, verdict, scope, or anchor terms")
    pr_search.add_argument("--state", action="append", choices=sorted(refutations.STATES),
                           help="filter by state; repeatable")
    pr_search.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_search.add_argument("--json", action="store_true", help="machine-readable output")
    pr_search.set_defaults(func=_cli._cmd_refute_search)

    pr_guard = refute_sub.add_parser(
        "guard", help="check active refutations by exact anchor or subject phrase")
    pr_guard.add_argument("query", nargs="+", help="the proposed approach or user prompt")
    pr_guard.add_argument("--anchor", action="append", default=[], metavar="ANCHOR",
                          help="candidate action anchor; repeatable")
    pr_guard.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_guard.add_argument("--json", action="store_true", help="machine-readable output")
    pr_guard.add_argument("--quiet", action="store_true",
                          help="print nothing when no active refutation matches")
    pr_guard.set_defaults(func=_cli._cmd_refute_guard)
