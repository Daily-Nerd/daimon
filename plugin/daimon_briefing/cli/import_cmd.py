"""`daimon import`: the bounded first fill (#1236, slices 2 and 3).

The command owns the run and nothing about the selection: `importer.
discover` finds the project's transcripts when none are given (newest
first, cut to `--sessions` here, default `importer.DEFAULT_SESSIONS`),
`importer.plan_import` decides the session list inside that window, this
module prints it, loops over it oldest first, applies the two stop rules
and prints the summary. Each
session goes through `cli._run_serialize(..., door="import")`, the one
capture pipeline, after `ledger.write_import_marker` has opened the attempt
in serialize.log, so every result line stays byte-identical to a live
capture's and `status`, `stats` and `heal` read an import as slice 1 taught
them to.

No silent spend (invariant 2): the plan prints the planned call count
before the first model call, `--plan` stops there, and the run stops before
the session whose planned calls would take the SPENT count past
`--max-calls` (default: the planned total). Spent is what the written
checkpoint's `capture_cost.calls` stamp says the backends answered, so a
validation resample, a parse retry or a rescue on the fallback backend all
count; the plan cannot see them, which is why a default run can pass its
printed number by at most one session's own retries and rescue, never by a
whole session. The first failed serialize of any kind stops the run (design
section 6); a resume is the same command again, and the plan skips done
sessions for free (design 4(d)). The summary prints on every exit after the
first serialize, with the spent calls beside the planned ones. Nothing here
retries, sleeps or swallows: a refusal before spend is exit 2, a stop on
failure is exit 1, a bounded stop and everything imported are exit 0.
"""

import os
import sys
import time
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import capture, config, importer, ledger, llm, provenance, serializer, store
from .. import render
from .serialize import _read_serialize_log

RESUME_HINT = (
    "run the same command again to resume; done sessions are skipped for free."
)


def _cost_of(checkpoint) -> tuple:
    """(`total_tokens`, `calls`) as the checkpoint's `capture_cost` stamp
    recorded them (#1235, #1236): tokens None when the stamp is absent or
    the backend reported nothing, calls None when the stamp predates the
    field."""
    cost = checkpoint.get("capture_cost") if isinstance(checkpoint, dict) else None
    if not isinstance(cost, dict):
        return None, None

    def _int(value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return _int(cost.get("total_tokens")), _int(cost.get("calls"))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _print_plan(plan: importer.Plan) -> None:
    name = Path(plan.project).name or plan.project
    hosts = sorted({s.host for s in plan.sessions})
    where = f" ({'+'.join(hosts)})" if hosts else ""
    print(
        f"import plan for {name}{where}: {_plural(len(plan.sessions), 'session')}, "
        f"about {_plural(plan.calls, 'model call')}"
    )
    for i, s in enumerate(plan.sessions, 1):
        line = (
            f"  {i}  {s.ended[:10]}  {s.messages} msgs  "
            f"{_plural(s.calls, 'call')}  {s.session_id}"
        )
        if s.notes:
            line += "  note: " + "; ".join(s.notes)
        print(line)
    for k in plan.skipped:
        print(f"  skipped {k.session_id}: {k.reason}")


def _cmd_import(args) -> int:
    project = _cli._resolve_project(args.project)
    if config.is_disabled():
        # The write boundary would refuse after the model call; refusing here
        # keeps the kill switch at zero spend.
        print(
            "refused: daimon disabled (DAIMON_DISABLE), nothing imported",
            file=sys.stderr,
        )
        return 2
    # D10.4: an unproven events ledger refuses every admission. Raises
    # jsonl.Refused, which `main` prints as `error: ...` at exit 2, before
    # a marker or a model call.
    store.admission_preflight(project)
    discovered = args.transcript is None
    if discovered:
        # The same Claude Code root `plan_import` checks paths against, so
        # discovery and the project check read one set of roots.
        resolver = provenance.SourceResolver(
            claude_projects=config.claude_projects_dir()
        )
        window = args.sessions if args.sessions is not None else importer.DEFAULT_SESSIONS
        found = importer.discover(project, resolver)
        paths = found.paths[:window]
    else:
        found = None
        paths = args.transcript
    try:
        plan = importer.plan_import(
            project,
            paths,
            now=time.time(),
            log_text=_read_serialize_log(),
            live_session_id=os.environ.get("CLAUDE_CODE_SESSION_ID"),
            explicit_project=args.project is not None,
        )
    except importer.ImportRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_plan(plan)
    if found is not None:
        for host, root in found.unreadable:
            print(f"  {host} root unreadable: {root}")
        print(importer.not_discovered_line())
    if args.plan:
        return 0
    if not plan.sessions:
        print("nothing to import")
        return 0
    bound = args.max_calls if args.max_calls is not None else plan.calls
    n = len(plan.sessions)
    imported = 0
    planned = 0
    spent = 0
    tokens = 0
    unreported = 0
    history = 0
    rescued: list = []
    rc = 0
    for i, s in enumerate(plan.sessions, 1):
        if spent + s.calls > bound:
            print(
                f"stopped before [{i}/{n}] {s.session_id}: its "
                f"{_plural(s.calls, 'planned call')} would pass --max-calls "
                f"{bound} ({spent} spent so far). {imported} of {n} "
                f"imported, {n - imported} left."
            )
            print(RESUME_HINT)
            break
        ledger.write_import_marker(s.session_id, project, str(s.path))
        # The session id goes along only when it is not what the file's own
        # name says (Kimi, #988), the rule heal applies for the same reason.
        session = s.session_id if s.session_id != s.path.stem else None
        start = time.monotonic()
        with render.working(f"importing {s.session_id}"):
            run_rc = _cli._run_serialize(
                s.path, project, session=session, door=capture.DOOR_IMPORT
            )
        took = int(time.monotonic() - start)
        written = store.read_checkpoint(s.session_id) if run_rc == 0 else None
        if written is None:
            print(
                f"[{i}/{n}] {s.session_id}: error after {took}s (the error "
                "line is above)"
            )
            print(
                f"stopped after the first failed session. {imported} of {n} "
                f"imported, {n - imported} left."
            )
            print(RESUME_HINT)
            rc = 1
            break
        imported += 1
        planned += s.calls
        total, calls = _cost_of(written)
        # A stamp without the calls field (none written by this code) would
        # leave the bound blind; count the plan for it rather than nothing.
        spent += calls if calls is not None else s.calls
        if total is None:
            unreported += 1
            line = f"[{i}/{n}] {s.session_id}: tokens unreported"
        else:
            tokens += total
            line = f"[{i}/{n}] {s.session_id}: {total:,} tokens"
        downgraded = serializer.quotes_downgraded()
        if downgraded:
            line += f", {_plural(downgraded, 'quote')} downgraded"
        if s.history:
            history += 1
            line += ", history only"
        if llm.fallback_used():
            rescued.append(s.session_id)
            line += " [fallback backend]"
        print(line)
    summary = (
        f"imported {imported} of {n}, {_plural(spent, 'call')} "
        f"({planned} planned), {tokens:,} tokens reported"
    )
    if unreported:
        summary += f", {_plural(unreported, 'session')} unreported"
    if history:
        summary += f", {history} history only"
    if rescued:
        summary += f", {len(rescued)} via fallback backend ({', '.join(rescued)})"
    print(summary)
    return rc


def _positive(flag: str):
    """An argparse type for a count of 1 or more, naming `flag` in its
    refusal; shared by `--max-calls` and `--sessions`."""
    import argparse

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a whole number: {raw!r}")
        if value < 1:
            raise argparse.ArgumentTypeError(f"{flag} needs a count of 1 or more")
        return value

    return parse


def register(sub, fmt) -> None:
    """Register `import` on the top-level subparsers."""
    p = sub.add_parser(
        "import",
        help="serialize past transcripts of one project in one bounded run; "
        "prints the plan and about how many model calls it costs "
        "before spending",
    )
    p.add_argument(
        "--project",
        help="project directory the transcripts belong to (default: "
        "DAIMON_PROJECT_DIR, then cwd); a transcript that records no "
        "project (Windsurf, Kimi) is accepted only when this flag is "
        "given",
    )
    which = p.add_mutually_exclusive_group()
    which.add_argument(
        "--transcript",
        action="append",
        metavar="PATH",
        help="a transcript file to import (repeatable); one that records a "
        "working directory must record this project's. Without it the "
        "project's transcripts are discovered on disk (see --sessions)",
    )
    which.add_argument(
        "--sessions",
        type=_positive("--sessions"),
        default=None,
        metavar="N",
        help="discover the project's N newest transcripts under the Claude "
        "Code and Codex roots (default: 5), newest by session end, matched "
        "on the working directory each file recorded; the skips the plan "
        "names come out of those N and nothing older takes their place, so "
        "the same command rebuilds the same plan. Windsurf and Kimi record "
        "no project and Gemini and Hermes write no transcript file: pass "
        "--transcript for those",
    )
    p.add_argument(
        "--max-calls",
        type=_positive("--max-calls"),
        default=None,
        metavar="N",
        help="stop before the session whose planned calls would take the "
        "spent count past N (default: the planned total); a run passes "
        "N only by one session's own retries and rescue",
    )
    p.add_argument(
        "--plan",
        action="store_true",
        help="print the plan and exit without a model call",
    )
    p.set_defaults(func=_cmd_import)
