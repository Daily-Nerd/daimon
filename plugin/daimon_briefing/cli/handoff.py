"""`daimon handoff` and `daimon log`: the baton and the freeform timeline event (moved out of cli/__init__.py, #1132 PR 5).

Every name is re-exported from `cli`, which stays the seam.
"""

import sys

import daimon_briefing.cli as _cli

from .. import render, store


# #523: a baton is small on purpose — "do X first, beware Y", not a second
# checkpoint. Over-cap input is REFUSED, never silently truncated: it is an
# authored artifact and the author trims it.
_HANDOFF_MAX_CHARS = 2000


def _cmd_handoff(args) -> int:
    """Leave (or retract) the project's baton (#523). Ref-less by contract —
    scar 0025: an event kind carrying an item_ref silently resolves that
    item, so a handoff must never name one. The resolutions fold ignores
    ref-less lines (guarded by test_handoff_event_never_resolves_an_item)."""
    _cli._note_usage("handoff")
    project = _cli._resolve_project(args.project)
    if args.clear:
        if args.text:
            print("error: --clear takes no text", file=sys.stderr)
            return 1
        if not store.append_event("", "cleared", note="", kind="handoff",
                                  project_dir=project):
            print("error: handoff not recorded (daimon disabled or project "
                  "unknown)", file=sys.stderr)
            return 1
        render.render_lifecycle_lines(["handoff cleared"])
        return 0
    text = (args.text or "").strip()
    if not text:
        print("error: nothing to hand off — pass the baton text or --clear",
              file=sys.stderr)
        return 1
    if len(text) > _HANDOFF_MAX_CHARS:
        # #902: a refusal that names no destination sends the trimmed content
        # to whatever store is nearest, which for an agent is the harness's
        # own memory file, where daimon never sees it. Name the daimon-side
        # homes. NOT `daimon log`: nothing reads a ref-less note back, so it
        # would be a void with a command name.
        print(f"error: baton exceeds {_HANDOFF_MAX_CHARS} chars "
              f"({len(text)}) — a handoff is \"do X first, beware Y\", not a "
              "second checkpoint; trim it. The trimmed facts belong in a "
              "checkpoint (the /daimon-end skill, `daimon write-checkpoint`), "
              "and a rule that must never decay in "
              "`daimon ruling propose --by agent`", file=sys.stderr)
        return 1
    # #571: latest-wins stays the contract, but replacing a baton no session
    # has consumed yet must not be silent — the superseded text never
    # surfaces again (ref-less events sit outside ranking/recall/carry).
    # active_handoff already encodes "unconsumed" (None after two distinct
    # non-introspection serializes) and is fail-open, so a broken read warns
    # about nothing rather than blocking the write.
    prior = store.active_handoff(project)
    if prior:
        print("warning: superseding an unconsumed baton — its text below "
              "never surfaces again; fold anything still relevant into the "
              f"new baton:\n  {prior['note']}", file=sys.stderr)
    if not store.append_event("", "active", note=text, kind="handoff",
                              project_dir=project):
        print("error: handoff not recorded (daimon disabled or project "
              "unknown)", file=sys.stderr)
        return 1
    render.render_lifecycle_lines(
        ["handoff recorded — will lead the next briefing for this project."])
    return 0


def _cmd_log(args) -> int:
    """Freeform zero-LLM event append (#102): a timeline fact worth keeping
    that is not tied to one item. The fold ignores ref-less lines; readers
    of the raw log get the audit trail."""
    if store.is_tombstone_status(args.status):
        print("a forgotten: status is a tombstone and only `daimon forget` "
              "writes one (it also removes the value) — refused, nothing "
              "written")
        return 1
    project = _cli._resolve_project(args.project)
    ok = store.append_event("", args.status, note=args.text,
                            kind=args.kind, project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    render.render_lifecycle_lines([f"logged [{args.kind}] {args.text}"])
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_handoff = sub.add_parser(
        "handoff",
        help="leave an authored baton for the next session — renders above "
             "everything in its next briefing (#523)",
    )
    p_handoff.add_argument("text", nargs="?", default=None,
                           help="the baton: imperative, small — what to do "
                                "first and what to beware")
    p_handoff.add_argument("--clear", action="store_true",
                           help="retract the active baton")
    p_handoff.add_argument("--project", help="project directory (default: "
                           "DAIMON_PROJECT_DIR, then cwd)")
    p_handoff.set_defaults(func=_cmd_handoff)

    p_log = sub.add_parser(
        "log", help="append a freeform timeline event (zero-LLM) to this project's event log (#102)",
    )
    p_log.add_argument("--text", required=True, help="what happened")
    p_log.add_argument("--kind", default="note", help="event kind (default: note)")
    p_log.add_argument("--status", default="", help="optional free-form status")
    p_log.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_log.set_defaults(func=_cmd_log)
