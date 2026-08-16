"""`daimon amend` verbs — evidence-carrying state transitions on briefed
items (#691).

Pure move out of the cli monolith (#708). Shared helpers that remain in the
package `__init__` are reached through the module object (`_cli.<name>`) so
the `cli.<name>` seam tests and hosts patch keeps working on moved code.
"""

import functools
import json
import sys

import daimon_briefing.cli as _cli

from .. import amendments, briefing, render, store


def _amend_channel(args) -> str:
    """The channel this invocation actually arrived through — the
    `_refute_channel` contract restated for the amendment ledger (same
    doctrine, its own error type so refusals stay per-surface)."""
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise amendments.AmendmentError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record a candidate, or run it from a terminal")
    return "cli-tty"


def _cmd_amend_propose(args) -> int:
    project = _cli._resolve_project(args.project)
    item_id = str(args.item_id or "").strip()
    # Exact-id binding against the LIVE checkpoint only — an amendment
    # describes an open item's state, so unlike forget it never reaches into
    # prev-N surfaces, and unlike resolve it never fuzzy-matches: the id came
    # off a rendered ` [id]` handle or it does not exist. Loop-shaped items
    # only (#480's scope rule, restated): amending a settled decision or
    # belief would invite exactly the state-rewriting on settled facts that
    # BRIEFABLE_ITEM_KEYS exists to fence off, and `daimon loops` — the
    # discovery surface this command's errors point at — lists only these.
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    live = {
        str(item.get("id") or "")
        for section, key in store._ITEM_LISTS
        if key in briefing.BRIEFABLE_ITEM_KEYS
        for item in ((checkpoint or {}).get(section) or {}).get(key) or []
        if isinstance(item, dict)
    }
    live.discard("")
    if item_id not in live:
        _cli._note_usage("amend:no-match")
        print(f"no open-loop item with id {item_id!r} — amend targets open "
              "questions and uncertainties; `daimon loops` lists them")
        return 1
    if store.is_resolved(store.resolutions(project_dir=project).get(item_id)):
        _cli._note_usage("amend:resolved")
        print(f"{item_id} is already resolved — an amendment describes an "
              "OPEN item; `daimon reverify` reopens one first")
        return 1
    try:
        a_id = amendments.propose(
            item_id=item_id, change=args.change, evidence=args.evidence,
            channel=_amend_channel(args), note=args.note or "",
            project_dir=project)
    except amendments.AmendmentError as exc:
        _cli._note_usage("amend:refused")
        print(f"amendment not recorded: {exc}")
        return 1
    record = amendments.get(a_id, project_dir=project)
    state = record["state"] if record else "candidate"
    _cli._note_usage("amend:agent" if getattr(args, "by", None) == "agent"
                     else "amend")
    render.render_ledger_lines(
        [f"amendment {a_id} recorded on {item_id}: {args.change} ({state})"])
    if state == "candidate":
        # Same posture as refute add: an agent-authored candidate is never
        # handed its own escalation command — verification is the transcript
        # byte-check at session end, settlement is a human's.
        render.render_ledger_lines(
            ["  evidence is byte-checked against the transcript at session "
             "end; a human settles it earlier with `daimon amend ratify` "
             "or `daimon amend reject`"])
    return 0


def _cmd_amend_verdict(args) -> int:
    project = _cli._resolve_project(args.project)
    verb = args.amend_cmd
    try:
        channel = _amend_channel(args)
        if verb == "ratify":
            amendments.ratify(args.amendment_id, channel=channel,
                              project_dir=project)
        else:
            amendments.reject(args.amendment_id, channel=channel,
                              note=getattr(args, "note", None) or "",
                              project_dir=project)
    except amendments.AmendmentError as exc:
        _cli._note_usage(f"amend:{verb}:refused")
        print(f"amendment {verb} refused: {exc}")
        return 1
    _cli._note_usage(f"amend:{verb}")
    record = amendments.get(args.amendment_id, project_dir=project)
    render.render_ledger_lines(
        [f"{args.amendment_id}: {record['state'] if record else 'unknown'}"])
    return 0


def _cmd_amend_list(args) -> int:
    project = _cli._resolve_project(args.project)
    _cli._note_usage("amend:list")
    rows = sorted(
        amendments.records(project_dir=project).values(),
        key=lambda r: (r["state"] != "candidate",
                       r.get("updated_at") or "", r["amendment_id"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        render.render_ledger_lines(["no amendments recorded for this project"])
        return 0
    lines = []
    for r in rows:
        quote = briefing._truncate_agent_claim(r.get("evidence"))
        lines.append(f'{r["amendment_id"]}  {r["state"]:<9} {r["item_id"]}  '
                     f'{r["change"]}: "{quote}"')
    render.render_ledger_lines(lines)
    return 0


def register(sub, fmt) -> None:
    """Register the `amend` parser family on the top-level subparsers."""
    p_amend = sub.add_parser(
        "amend",
        help="record an evidence-carrying state transition on a briefed item (#691)",
        epilog="Examples:\n"
               "  daimon amend o-1a2b3c4d5e6f --change progressed "
               "--evidence 'the PR merged' --by agent\n"
               "  daimon amend ratify a-0f1e2d3c4b5a\n",
    )
    amend_sub = p_amend.add_subparsers(dest="amend_cmd", required=True)
    amend_sub.add_parser = functools.partial(
        amend_sub.add_parser, formatter_class=fmt)

    pa_prop = amend_sub.add_parser(
        "propose",
        help="propose an amendment; agent proposals stay candidates until "
             "the session-end byte-check or a human verdict")
    pa_prop.add_argument("item_id",
                         help="exact item id from a briefing/loops handle")
    pa_prop.add_argument(
        "--change", required=True, choices=sorted(amendments.CHANGES),
        help="the typed transition; the closed vocabulary is the render bound")
    pa_prop.add_argument(
        "--evidence", required=True,
        help="verbatim transcript quote backing the change; byte-checked "
             "against this session's transcript at session end")
    pa_prop.add_argument("--note",
                         help="short context; human channel only")
    pa_prop.add_argument("--by", choices=["agent"], default=None,
                         help="declare yourself an agent; omit it only from "
                              "an interactive terminal, which is the human path")
    pa_prop.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_prop.set_defaults(func=_cli._cmd_amend_propose)

    pa_ratify = amend_sub.add_parser(
        "ratify", help="activate a candidate or verified amendment as a human decision")
    pa_ratify.add_argument("amendment_id", help="exact a-… id")
    pa_ratify.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; ratification then "
                                "refuses, because it requires a human channel")
    pa_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_ratify.set_defaults(func=_cli._cmd_amend_verdict)

    pa_reject = amend_sub.add_parser(
        "reject", help="reject an amendment with a reason, as a human decision")
    pa_reject.add_argument("amendment_id", help="exact a-… id")
    pa_reject.add_argument("--note", help="why it is wrong; kept on the record")
    pa_reject.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; rejection then "
                                "refuses, because it requires a human channel")
    pa_reject.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_reject.set_defaults(func=_cli._cmd_amend_verdict)

    pa_list = amend_sub.add_parser("list", help="list project amendments, candidates first")
    pa_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_list.add_argument("--json", action="store_true", help="machine-readable output")
    pa_list.set_defaults(func=_cli._cmd_amend_list)
