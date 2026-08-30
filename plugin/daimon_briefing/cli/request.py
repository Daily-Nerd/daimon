"""`daimon request` verbs — one project's ask of another (#694, PR 1).

The object and its verbs. The inbox (`request inbox`, the briefing panel,
the surfaced stamps) is PR 2, so what this family gives today is the
pull-only view: open an ask, revise it, list what this bucket holds, and
land the human verdicts.

Shared helpers that live in the package `__init__` are reached through the
module object (`_cli.<name>`) so the `cli.<name>` seam tests and hosts patch
keeps working, the same contract the amend/ruling families follow.
"""

import argparse
import difflib
import functools
import json
import sys

import daimon_briefing.cli as _cli

from .. import config, render, requests, store

# Verdicts land under their own verb names; the ledger's event vocabulary
# spells one of them with an underscore.
_VERDICT_CALLS = {
    "accept": "accept",
    "reject": "reject",
    "needs-info": "needs_info",
    "suppress": "suppress",
}
_MARKS = {"open": "→", "needs-info": "?", "accepted": "✓",
          "rejected": "×", "done": "✔", "stale": "⏳"}
# Near-match budget for an unknown `--to`: enough to catch a typo, few
# enough that the refusal stays a refusal rather than a project directory.
_SUGGESTIONS = 3


def _request_channel(args) -> str:
    """The channel this invocation actually arrived through — the
    `_refute_channel` contract restated for the request ledger (same
    doctrine, its own error type so refusals stay per-surface)."""
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise requests.RequestError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record it as an agent, or run it from a "
            "terminal")
    return "cli-tty"


def _state_label(record: dict, project_dir=None) -> str:
    """#694 D8: an agent's completion claim is never rendered as fact — the
    session-end byte-check drops the qualifier once `verify_done` clears
    `done_claimed`. D3: an open/needs-info record whose surfaced anchor has
    aged past STALE_AFTER_SESSIONS renders `stale` instead — never written
    to disk, recomputed fresh on every call via `requests.render_state`."""
    if record.get("state") == "done" and record.get("done_claimed"):
        return "done (claimed, unverified)"
    return requests.render_state(record, project_dir=project_dir)


def _request_lines(record: dict, project_dir=None) -> list:
    """One request as a record card. The header follows the #711 three-span
    shape (state bracket, id, prose); the body carries the two-party facts a
    one-liner would drop — who it is addressed to, why, and whether the
    sender is blocked on it."""
    state = requests.render_state(record, project_dir=project_dir)
    who = record.get("verdict_label") or f"{record.get('opened_by', '?')}-asked"
    lines = [f"[{_MARKS.get(state, '?')} "
             f"{_state_label(record, project_dir=project_dir)} · {who}] "
             f"{record['request_id']}  {record.get('ask', '')}"]
    to = record.get("to", "")
    lines.append(f"  To: {to}" + (" (for a human)" if record.get("to_human")
                                  else ""))
    if record.get("from_label"):
        lines.append(f"  From: {record['from_label']}")
    lines.append(f"  Why: {record.get('why', '')}")
    if record.get("evidence"):
        lines.append(f"  Evidence: {record['evidence']}")
    if record.get("supersedes"):
        lines.append(f"  Supersedes: {record['supersedes']}")
    if record.get("blocking"):
        lines.append("  Blocking: the sender is waiting on this")
    if record.get("suppressed"):
        # D5: say what suppression did and did not take away, on the surface
        # that proves it — the record is right here.
        lines.append("  Suppressed from the briefing panel; still listed "
                     "here, and any verdict reverses it")
    if record.get("note"):
        lines.append(f"  Note: {record['note']}")
    if record.get("done_evidence"):
        lines.append(f"  Done: {record['done_evidence']}")
    if record.get("revision"):
        lines.append(f"  Revisions: {record['revision']} of "
                     f"{requests.MAX_REVISIONS}")
    return lines


def _inbox_lines(record: dict, project_dir=None) -> list:
    """One inbox entry as a record card — same three-span header shape as
    `_request_lines`, labeled with the foreign SENDER (`From:`) instead of
    the local `to`, since every row here was addressed to THIS project."""
    state = requests.render_state(record, project_dir=project_dir)
    lines = [f"[{_MARKS.get(state, '?')} "
             f"{_state_label(record, project_dir=project_dir)}] "
             f"{record['request_id']}  {record.get('ask', '')}"]
    lines.append(f"  From: {record.get('from_label') or '?'}"
                 + (" (for a human)" if record.get("to_human") else ""))
    lines.append(f"  Why: {record.get('why', '')}")
    if record.get("evidence"):
        lines.append(f"  Evidence: {record['evidence']}")
    if record.get("supersedes"):
        lines.append(f"  Supersedes: {requests.supersedes_label(record)}")
    if record.get("blocking"):
        lines.append("  Blocking: the sender is waiting on this")
    if record.get("suppressed"):
        lines.append("  Suppressed from the briefing panel; still listed "
                     "here, and any verdict reverses it")
    if record.get("note"):
        lines.append(f"  Note: {record['note']}")
    if record.get("done_evidence"):
        lines.append(f"  Done: {record['done_evidence']}")
    if record.get("revision"):
        lines.append(f"  Revisions: {record['revision']} of "
                     f"{requests.MAX_REVISIONS}")
    return lines


def _cmd_request_inbox(args) -> int:
    project = _cli._resolve_project(args.project)
    rows = requests.inbox_listing(project_dir=project)
    _cli._note_usage("request:inbox")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        render.render_ledger_lines(["no requests addressed to this project"])
        return 0
    render.render_ledger_records(
        [_inbox_lines(row, project_dir=project) for row in rows])
    return 0


def _inject_lines(record: dict) -> list[str]:
    """One delivered ask, compressed to what decides it. Deliberately
    thinner than `_inbox_lines`: this lands mid-session on the agent's
    context budget, not on a surface someone opened to read."""
    lines = [f"daimon request: {record['request_id']} from "
             f"{record.get('from_label') or '?'}"
             + (" (for a human)" if record.get("to_human") else "")
             + (" [blocking: the sender is waiting]"
                if record.get("blocking") else "")]
    lines.append(f"  Ask: {record.get('ask', '')}")
    if record.get("why"):
        lines.append(f"  Why: {record['why']}")
    return lines


_INJECT_VERDICT_MARKS = {"needs-info": "?", "accepted": "✓",
                         "rejected": "×", "done": "✔"}


def _verdict_inject_lines(record: dict) -> list[str]:
    """One delivered verdict, compressed to what the sender acts on. Same
    posture as `_inject_lines`: this lands mid-session on the agent's context
    budget. The note is the recipient's answer to THIS project's own ask, so
    it is this project's mail rather than a foreign record being rendered."""
    state = str(record.get("state") or "")
    mark = _INJECT_VERDICT_MARKS.get(state, "?")
    lines = [f"daimon verdict: {mark} {state}  {record['request_id']} "
             f"(to {record.get('to') or '?'})"]
    lines.append(f"  Ask: {record.get('ask', '')}")
    note = str(record.get("note") or "").strip()
    if note:
        lines.append(f"  Note: {note}")
    evidence = str(record.get("done_evidence") or "").strip()
    if evidence:
        lines.append(f"  Done: {evidence}")
    return lines


def _cmd_request_inject(args) -> int:
    """Print the undecided asks this session has not been shown, or nothing.

    rc 0 ALWAYS. This sits on the user's per-prompt critical path, exactly
    like `recall-inject`, and a nudge is never worth blocking a prompt on —
    every failure below is silent by design.

    The stamp is written AFTER the render, the same ordering the brief's
    `surfaced` stamp uses: a crash between printing and stamping re-delivers
    next turn (a duplicate nudge) rather than recording a delivery that
    never reached anyone."""
    if not config.live_delivery_enabled():
        return 0
    session = str(getattr(args, "session", None) or "").strip()
    if not session:
        return 0
    _cli._note_usage("request-inject")
    try:
        project = _cli._resolve_project(args.project)
        entry = requests.deliverable(session, project_dir=project)
        verdicts = requests.verdict_deliverable(session, project_dir=project)
        rows = entry["rows"]
        if not rows and not verdicts["rows"]:
            return 0
        out = []
        for record in verdicts["rows"]:
            out.extend(_verdict_inject_lines(record))
        if verdicts["overflow"]:
            plural = "s" if verdicts["overflow"] != 1 else ""
            out.append(f"(+{verdicts['overflow']} more decided{plural}, "
                       "not shown here)")
        for record in rows:
            out.extend(_inject_lines(record))
        # #800: name what the cap withheld, in the panel's own words. Without
        # it a fourth addressed ask is dropped and reads as an absence, which
        # is the one failure this feature cannot have.
        overflow = entry["overflow"]
        if overflow:
            plural = "s" if overflow != 1 else ""
            out.append(f"(+{overflow} more waiting{plural}, not shown here)")
        # The verbs that decide are human-only (D8); the line names the
        # surface that lists them rather than a verb the agent cannot run.
        # Only when there ARE undecided asks: a delivery carrying nothing but
        # verdicts has nothing awaiting a decision to point at.
        if rows:
            out.append("Undecided. Full records: `daimon request inbox`")
        print("\n".join(out))
        for record in verdicts["rows"]:
            requests.stamp_verdict_delivered(record["request_id"], session,
                                             project_dir=project)
        for record in rows:
            requests.stamp_delivered(record["request_id"], session,
                                     project_dir=project)
    except Exception:  # noqa: BLE001 — per-prompt path: never block a prompt
        return 0
    return 0


def _resolve_to(raw) -> str:
    """The recipient slug, from either spelling the user can type.

    `store.project_slug` munges every non-word char to '-', so a REAL slug
    always begins with one — and argparse reads a leading dash as an option,
    which makes `--to <slug>` unusable for exactly the values that exist
    (`--to=<slug>` still works, and the help says so). Accepting the project
    DIRECTORY is the spelling that survives a bare space: the transform is
    idempotent, so a slug passed here comes back unchanged and a path comes
    back as its slug.
    """
    return store.project_slug(str(raw or "").strip()) or ""


def _cmd_request_open(args) -> int:
    project = _cli._resolve_project(args.project)
    to = _resolve_to(args.to)
    known = {b["slug"] for b in store.list_buckets()}
    if to not in known and not args.anyway:
        # D4: an unknown slug is not a typo the ledger can absorb silently —
        # such a record renders "never surfaced" forever and never decays,
        # so the refusal names the shape before the write, not after.
        _cli._note_usage("request:open:unknown-to")
        print(f"no daimon bucket named {to!r} — that project has never "
              "serialized a session on this machine")
        near = difflib.get_close_matches(to, sorted(known), n=_SUGGESTIONS)
        if near:
            print(f"  did you mean: {', '.join(near)}")
        print("  or re-run with --anyway to record the ask regardless")
        return 1
    try:
        q_id = requests.open_request(
            to=to, ask=args.ask, why=args.why, channel=_request_channel(args),
            blocking=bool(args.blocking), to_human=bool(args.to_human),
            evidence=args.evidence or "", supersedes=args.supersedes or "",
            project_dir=project)
    except requests.RequestError as exc:
        _cli._note_usage("request:open:refused")
        print(f"request not recorded: {exc}")
        return 1
    _cli._note_usage("request:open:agent"
                     if getattr(args, "by", None) == "agent"
                     else "request:open")
    if to not in known:
        render.render_ledger_lines(
            [f"warning: {to} has no bucket on this machine, so this ask is "
             "never surfaced there and never decays; it stays in "
             "`daimon request list` until that project serializes a session"])
    record = requests.get(q_id, project_dir=project)
    render.render_ledger_lines(
        _request_lines(record, project_dir=project) if record else
        [f"request {q_id} recorded"])
    return 0


def _cmd_request_revise(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        requests.revise(args.request_id, channel=_request_channel(args),
                        ask=args.ask, why=args.why, evidence=args.evidence,
                        project_dir=project)
    except requests.RequestError as exc:
        _cli._note_usage("request:revise:refused")
        print(f"request not revised: {exc}")
        return 1
    _cli._note_usage("request:revise")
    # #857: `revise()` above read the record through _require, and this is a
    # SECOND read. A writer between the two makes it None, and rendering a
    # card from None dies inside render_state rather than saying anything.
    # Both sibling paths already handle this; only revise did not.
    #
    # The revision itself landed before the record went, so the line reports
    # that. Saying nothing, or failing, would describe the wrong half of what
    # happened.
    record = requests.get(args.request_id, project_dir=project)
    if record is None:
        render.render_ledger_lines(
            [f"{args.request_id}: revision recorded in this project's ledger",
             "  the record is not readable from this bucket right now — the "
             "revision is on the ledger and renders with the request"])
        return 0
    render.render_ledger_lines(_request_lines(record, project_dir=project))
    return 0


def _cmd_request_verdict(args) -> int:
    project = _cli._resolve_project(args.project)
    verb = args.request_cmd
    try:
        channel = _request_channel(args)
        getattr(requests, _VERDICT_CALLS[verb])(
            args.request_id, channel=channel, note=args.note or "",
            project_dir=project)
    except requests.RequestError as exc:
        _cli._note_usage(f"request:{verb}:refused")
        print(f"request {verb} refused: {exc}")
        return 1
    _cli._note_usage(f"request:{verb}")
    _report(args.request_id, project, verb)
    return 0


def _report(request_id: str, project, verb: str) -> None:
    """Print the answered record, or say plainly that this bucket cannot see
    it. A recipient's answer to a foreign ask is written here and pairs with
    its origin only at the read-time join (PR 2) — until then the row is real
    and the record is not, and printing an empty card would hide that."""
    record = requests.get(request_id, project_dir=project)
    if record is None:
        render.render_ledger_lines(
            [f"{request_id}: {verb} recorded in this project's ledger",
             "  no matching request in this bucket — an answer to another "
             "project's ask joins its origin at read time, and until then "
             "it renders nowhere"])
        return
    render.render_ledger_lines(_request_lines(record, project_dir=project))


def _cmd_request_done(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        requests.done(args.request_id, channel=_request_channel(args),
                      evidence=args.evidence, project_dir=project)
    except requests.RequestError as exc:
        _cli._note_usage("request:done:refused")
        print(f"request done refused: {exc}")
        return 1
    _cli._note_usage("request:done")
    _report(args.request_id, project, "done")
    return 0


def _cmd_request_list(args) -> int:
    project = _cli._resolve_project(args.project)
    rows = requests.listing(project_dir=project)
    _cli._note_usage("request:list")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        render.render_ledger_lines(["no requests for this project"])
        return 0
    render.render_ledger_records(
        [_request_lines(row, project_dir=project) for row in rows])
    return 0


def register(sub, fmt) -> None:
    """Register the `request` parser family on the top-level subparsers."""
    p_request = sub.add_parser(
        "request",
        help="ask another project for something, and answer what it asks "
             "(#694)",
        epilog="Examples:\n"
               "  daimon request open --to ~/code/api "
               "--ask 'publish the 0.32 schema' "
               "--why 'the client cannot ship without it' --by agent\n"
               "  daimon request list\n"
               "  daimon request accept q-1a2b3c4d5e6f\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    request_sub = p_request.add_subparsers(dest="request_cmd", required=True)
    request_sub.add_parser = functools.partial(
        request_sub.add_parser, formatter_class=fmt)

    def _common(parser):
        parser.add_argument(
            "--project",
            help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")

    rq_open = request_sub.add_parser(
        "open", help="open a request addressed to another project's slug")
    rq_open.add_argument(
        "--to", required=True, metavar="PROJECT",
        help="recipient project directory, or its slug written as "
             "--to=<slug> (a slug starts with '-', which a bare --to would "
             "read as an option); `daimon projects` lists both")
    rq_open.add_argument("--ask", required=True,
                         help="what you are asking that project to do")
    rq_open.add_argument("--why", required=True,
                         help="why it matters; the recipient reads this first")
    rq_open.add_argument(
        "--to-human", action="store_true",
        help="the ask is for that project's human, not its agent")
    rq_open.add_argument(
        "--blocking", action="store_true",
        help="mark the sender as blocked on it; a flag on the record, never "
             "a claim on the recipient's time")
    rq_open.add_argument(
        "--evidence",
        help="optional source backing the ask; recorded, never resolved")
    rq_open.add_argument(
        "--supersedes", metavar="REQUEST_ID",
        help="the earlier q-… this ask replaces; the lineage renders always")
    rq_open.add_argument(
        "--anyway", action="store_true",
        help="record the ask even when the recipient slug has no bucket yet; "
             "it is never surfaced there until that project serializes")
    rq_open.add_argument("--by", choices=["agent"], default=None,
                         help="declare yourself an agent; omit it only from "
                              "an interactive terminal, which is the human path")
    _common(rq_open)
    rq_open.set_defaults(func=_cli._cmd_request_open)

    rq_revise = request_sub.add_parser(
        "revise", help="answer a needs-info or sharpen an open ask; capped "
                       f"at {requests.MAX_REVISIONS} revisions per record")
    rq_revise.add_argument("request_id", help="exact q-… id")
    rq_revise.add_argument("--ask", help="replacement ask")
    rq_revise.add_argument("--why", help="replacement rationale")
    rq_revise.add_argument("--evidence", help="replacement source")
    rq_revise.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; omit it only from "
                                "an interactive terminal")
    _common(rq_revise)
    rq_revise.set_defaults(func=_cli._cmd_request_revise)

    for verb, blurb in (
            ("accept", "accept an addressed request, as a human decision"),
            ("reject", "reject it with a reason; rejection is final for that "
                       "record, and the sender can open a new one citing it"),
            ("needs-info", "ask the sender for more before deciding"),
            ("suppress", "drop it out of the briefing panel; it stays in "
                         "`request list`, and any later verdict reverses it")):
        parser = request_sub.add_parser(verb, help=blurb)
        parser.add_argument("request_id", help="exact q-… id")
        parser.add_argument("--note", help="kept on the record")
        parser.add_argument(
            "--by", choices=["agent"], default=None,
            help="declare yourself an agent; the call then refuses, because "
                 "this verb requires a human channel")
        _common(parser)
        parser.set_defaults(func=_cli._cmd_request_verdict)

    rq_done = request_sub.add_parser(
        "done", help="report the ask as satisfied; an agent claim renders as "
                     "claimed-and-unverified until the byte-check")
    rq_done.add_argument("request_id", help="exact q-… id")
    rq_done.add_argument("--evidence", required=True,
                         help="what settles it; a verbatim quote from an "
                              "agent is byte-checked at session end")
    rq_done.add_argument("--by", choices=["agent"], default=None,
                         help="declare yourself an agent; omit it only from "
                              "an interactive terminal")
    _common(rq_done)
    rq_done.set_defaults(func=_cli._cmd_request_done)

    rq_list = request_sub.add_parser(
        "list", help="list this project's requests, undecided first")
    rq_list.add_argument("--json", action="store_true",
                         help="machine-readable output")
    _common(rq_list)
    rq_list.set_defaults(func=_cli._cmd_request_list)

    rq_inbox = request_sub.add_parser(
        "inbox", help="requests addressed TO this project, from every "
                      "sender, undecided first")
    rq_inbox.add_argument("--json", action="store_true",
                          help="machine-readable output")
    _common(rq_inbox)
    rq_inbox.set_defaults(func=_cli._cmd_request_inbox)
