"""`daimon trust` verbs — the human-only quarantine ledger (#1109 Slice 1).

Nothing here changes what a briefing, recall, or MCP tool renders — this
slice is write-only. `propose` records a candidate (or, from a human
channel, an immediately active quarantine); `confirm`/`dismiss`/`release`
are human-only, refused at the library boundary (`trust._human_transition`)
for any other channel, not merely hidden from this CLI.
"""

import functools
import json
import sys

from .. import render, trust
from . import _cap_refusal

# #920-style destination for an over-cap field: `reason` is the human's own
# rationale, capped the same way a ruling's `verdict` is — the long form
# belongs in the artifact it governs, never in the ledger row.
_DESTINATION_BY_FIELD = {
    "reason": (
        " — the long form belongs in the artifact it governs (the issue, "
        "the transcript, the design record); the record carries the "
        "one-paragraph verdict and a pointer."
    ),
    "evidence": (
        " — evidence is a pointer to the proof (a commit, a PR, a path, a "
        "verbatim line), not the proof itself."
    ),
}


def _refusal_message(prefix: str, exc: trust.TrustError) -> str:
    return _cap_refusal.format_cap_refusal(
        prefix, exc, trust.TrustTooLong, _DESTINATION_BY_FIELD)


def _trust_channel(args) -> str:
    """The channel this invocation actually arrived through.

    `--by agent` is a self-declaration of the NARROWER authority (the
    `_refute_channel` contract restated here): a human path has to show an
    interactive terminal, and the CLI can mint nothing stronger. Exposed on
    every trust verb, including confirm/dismiss/release — `--by agent`
    there is refused by `trust._human_transition` itself, the same way
    `daimon ruling ratify --by agent` is refused by the ledger, not hidden
    from the parser."""
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise trust.TrustError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record a candidate, or run it from a "
            "terminal")
    return "cli-tty"


def _record_line(record: dict) -> str:
    mark = {"candidate": "?", "active": "⛔", "dismissed": "×",
           "released": "✓"}.get(record["state"], "?")
    return (f"[{mark} {record['state']}] {record['quarantine_id']}  "
            f"{record.get('kind')}  {record.get('reason', '')}")


def _cmd_trust_propose(args) -> int:
    project = _resolve_project(args.project)
    try:
        tid = trust.propose(
            text=args.text, kind=args.kind, reason=args.reason,
            evidence=list(args.evidence or []), item_id=args.item_id or "",
            channel=_trust_channel(args), project_dir=project)
    except trust.TrustError as exc:
        print(_refusal_message("quarantine not recorded", exc))
        return 1
    record = trust.get(tid, project_dir=project)
    render.render_ledger_lines([_record_line(record)] if record else
                               [f"quarantine {tid} recorded"])
    if record and record["state"] == "candidate":
        render.render_ledger_lines(
            [f"  a human settles it with `daimon trust confirm {tid}` or "
             f"`daimon trust dismiss {tid}`"])
    return 0


def _cmd_trust_verdict(args) -> int:
    project = _resolve_project(args.project)
    verb = args.trust_cmd
    verb_fn = {"confirm": trust.confirm, "dismiss": trust.dismiss,
              "release": trust.release}[verb]
    try:
        channel = _trust_channel(args)
        verb_fn(args.quarantine_id, channel=channel, project_dir=project)
    except trust.TrustError as exc:
        print(_refusal_message(f"quarantine {verb} refused", exc))
        return 1
    record = trust.get(args.quarantine_id, project_dir=project)
    render.render_ledger_lines(
        [f"{args.quarantine_id}: {record['state'] if record else 'unknown'}"])
    return 0


def _cmd_trust_list(args) -> int:
    project = _resolve_project(args.project)
    rows = sorted(
        trust.records(project_dir=project).values(),
        key=lambda r: (r["state"] != "candidate",
                       r.get("updated_at") or "", r["quarantine_id"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        render.render_ledger_lines(["no quarantines recorded for this project"])
        return 0
    render.render_ledger_lines([_record_line(r) for r in rows])
    return 0


def _cmd_trust_show(args) -> int:
    project = _resolve_project(args.project)
    record = trust.get(args.quarantine_id, project_dir=project)
    if record is None:
        print(f"no quarantine found for {args.quarantine_id!r}")
        return 1
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    lines = [_record_line(record), f"  Value key: {record['value_key']}"]
    if record.get("item_id"):
        lines.append(f"  Item: {record['item_id']}")
    lines.append(f"  Scope: {record.get('scope_slug', '')}")
    for item in record.get("evidence") or []:
        lines.append(f"  Evidence: {item}")
    render.render_ledger_lines(lines)
    return 0


def _resolve_project(project):
    # Imported lazily to avoid a cycle with the `cli` package `__init__`
    # (mirrors `amend.py`'s own `daimon_briefing.cli as _cli` seam).
    import daimon_briefing.cli as _cli
    return _cli._resolve_project(project)


def register(sub, fmt) -> None:
    """Register the `trust` parser family on the top-level subparsers."""
    import daimon_briefing.cli as _cli

    p_trust = sub.add_parser(
        "trust",
        help="record or settle a human quarantine on a checkpoint value "
             "(#1109); write-only in this release, nothing reads it yet",
        epilog="Examples:\n"
               "  daimon trust propose --text \"the runbook was fabricated\" "
               "--kind decision --reason \"no matching PR\" "
               "--evidence issue:1109 --by agent\n"
               "  daimon trust confirm tr-0123456789ab\n"
               "  daimon trust release tr-0123456789ab\n",
    )
    trust_sub = p_trust.add_subparsers(dest="trust_cmd", required=True)
    trust_sub.add_parser = functools.partial(
        trust_sub.add_parser, formatter_class=fmt)

    pt_prop = trust_sub.add_parser(
        "propose",
        help="propose a quarantine; agent proposals stay candidates until "
             "a human confirms or dismisses them")
    pt_prop.add_argument("--text", required=True,
                         help="the checkpoint value to quarantine, verbatim")
    pt_prop.add_argument("--kind", required=True, choices=sorted(trust.KINDS),
                         help="which checkpoint field kind this concerns")
    pt_prop.add_argument("--reason", required=True,
                         help="why this value is unsafe to act on")
    pt_prop.add_argument("--evidence", action="append", required=True,
                         help="typed evidence source (repeatable): "
                              "message:<id>, transcript:<session>, "
                              "artifact:<path>, issue:<number>, "
                              "measurement:<receipt>, receipt:<id>, "
                              "url:<source>")
    pt_prop.add_argument("--item-id",
                         help="the checkpoint item id this was observed on "
                              "(an accelerator only — identity is the "
                              "value, not this id)")
    pt_prop.add_argument("--by", choices=["agent"], default=None,
                         help="declare yourself an agent; omit it only from "
                              "an interactive terminal, which is the human "
                              "path and lands active immediately")
    pt_prop.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pt_prop.set_defaults(func=_cli._cmd_trust_propose)

    for verb, help_text in (
            ("confirm", "activate a candidate quarantine as a human decision"),
            ("dismiss", "reject a candidate quarantine as a human decision"),
            ("release", "lift an active quarantine as a human decision")):
        pt_verb = trust_sub.add_parser(verb, help=help_text)
        pt_verb.add_argument("quarantine_id", help="exact tr-… id")
        pt_verb.add_argument(
            "--by", choices=["agent"], default=None,
            help=f"declare yourself an agent; {verb} then refuses, because "
                 "it requires a human channel")
        pt_verb.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
        pt_verb.set_defaults(func=_cli._cmd_trust_verdict)

    pt_list = trust_sub.add_parser(
        "list", help="list project quarantines, candidates first")
    pt_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pt_list.add_argument("--json", action="store_true", help="machine-readable output")
    pt_list.set_defaults(func=_cli._cmd_trust_list)

    pt_show = trust_sub.add_parser("show", help="show one quarantine record")
    pt_show.add_argument("quarantine_id", help="exact tr-… id")
    pt_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pt_show.add_argument("--json", action="store_true", help="machine-readable output")
    pt_show.set_defaults(func=_cli._cmd_trust_show)
