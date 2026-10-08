"""`daimon relations`: list, show and verdict on item relations (moved out of cli/__init__.py, #1132 PR 5).

Named `relations_cmd` because `cli.relations` is the library module the CLI
reaches through its namespace. Every name is re-exported from `cli`.
"""

import functools
import json
import sys

import daimon_briefing.cli as _cli

from .. import relations, render


def _relations_channel() -> str:
    """The observed write channel for a relation verdict.

    Narrower than `_refute_channel` on purpose: there is no `--by agent`
    here because agents cannot verdict relations AT ALL — the fold ignores
    non-human channels and the module refuses them, so offering the flag
    would only advertise a path that always fails. A verdict has to show an
    interactive terminal; anything else is refused, not downgraded.
    """
    if not sys.stdin.isatty():
        raise relations.RelationError(
            "relation verdicts are human-only and need an interactive "
            "terminal; there is no agent path to confirm, reject, or retract")
    return "cli-tty"


def _relations_endpoint_texts(project_dir) -> dict:
    """Stable cli seam over the engine's read-time id→text join."""
    return relations.endpoint_texts(project_dir)


def _cmd_relations_list(args) -> int:
    project = _cli._resolve_project(args.project)
    # Sort, state filter, and erased-edge withholding all live in
    # relations.listing — the presentation contract shared with the viewer
    # lane, so the two surfaces cannot drift. argparse `choices` already
    # gates unknown states.
    rows, withheld = relations.listing(
        states=set(args.state or relations.STATES), project_dir=project)
    _cli._note_usage("relations:list")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        if withheld:
            print(f"{withheld} edge(s) withheld (erased endpoint)")
    else:
        texts = _relations_endpoint_texts(project) if rows else {}
        render.render_relations_list(rows, texts, withheld)
    return 0


def _cmd_relations_show(args) -> int:
    project = _cli._resolve_project(args.project)
    record = relations.get(args.relation_id, project_dir=project)
    if record is None:
        print(f"unknown relation: {args.relation_id}")
        return 1
    _cli._note_usage("relations:show")
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        render.render_relation(record, _relations_endpoint_texts(project))
    return 0


def _cmd_relations_verdict(args) -> int:
    project = _cli._resolve_project(args.project)
    _cli.require_ledger(project, "relations.jsonl")
    move = {"confirm": relations.confirm, "reject": relations.reject,
            "retract": relations.retract}[args.verdict]
    try:
        move(args.relation_id, channel=_relations_channel(),
             project_dir=project)
    except relations.RelationError as exc:
        print(f"relation {args.verdict} refused: {exc}")
        return 1
    _cli._note_usage(f"relations:{args.verdict}")
    state = relations.records(project_dir=project)[args.relation_id]["state"]
    print(f"{args.relation_id} -> {state}")
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_relations = sub.add_parser(
        "relations",
        help="inspect and decide typed item relations (#678, shadow mode)",
        epilog="Examples:\n"
               "  daimon relations list\n"
               "  daimon relations show rel-0123456789abcdef\n"
               "  daimon relations confirm rel-0123456789abcdef\n",
    )
    relations_sub = p_relations.add_subparsers(dest="relations_cmd",
                                               required=True)
    relations_sub.add_parser = functools.partial(  # type: ignore[method-assign]
        relations_sub.add_parser, formatter_class=fmt)

    prl_list = relations_sub.add_parser(
        "list", help="candidates first; endpoint texts resolved at read time")
    prl_list.add_argument(
        "--state", action="append",
        choices=sorted(relations.STATES),
        help="filter by state; repeatable (default: all)")
    prl_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    prl_list.add_argument("--json", action="store_true", help="machine-readable output")
    prl_list.set_defaults(func=_cmd_relations_list)

    prl_show = relations_sub.add_parser(
        "show", help="one relation with its proposal history")
    prl_show.add_argument("relation_id", help="exact rel-… id")
    prl_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    prl_show.add_argument("--json", action="store_true", help="machine-readable output")
    prl_show.set_defaults(func=_cmd_relations_show)

    for verdict, blurb in (
            ("confirm", "record a human confirmation of a candidate edge"),
            ("reject", "record a human rejection; sticky against re-proposal"),
            ("retract", "undo a confirmation; a fresh proposal may revive it")):
        prl_verdict = relations_sub.add_parser(
            verdict,
            help=f"{blurb} (human-only: needs an interactive terminal)")
        prl_verdict.add_argument("relation_id", help="exact rel-… id")
        prl_verdict.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
        prl_verdict.set_defaults(func=_cmd_relations_verdict, verdict=verdict)
