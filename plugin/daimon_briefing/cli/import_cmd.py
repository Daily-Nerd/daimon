"""`daimon import`: the bounded first fill (#1236, slice 2, path-given).

The command owns the run and nothing about the selection: `importer.
plan_import` decides the session list, this module prints it, loops over it
oldest first, applies the two stop rules and prints the summary. Each
session goes through `cli._run_serialize(..., door="import")`, the one
capture pipeline, after `ledger.write_import_marker` has opened the attempt
in serialize.log, so every result line stays byte-identical to a live
capture's and `status`, `stats` and `heal` read an import as slice 1 taught
them to.

No silent spend (invariant 2): the plan prints the call count before the
first model call, `--plan` stops there, and the run stops before the session
that would pass `--max-calls` (default: the planned count, so a default run
cannot exceed what it printed). The first failed serialize of any kind stops
the run (design section 6); a resume is the same command again, and the
plan skips done sessions for free (design 4(d)). Nothing here retries,
sleeps or swallows: a refusal before spend is exit 2, a stop on failure is
exit 1, everything imported or nothing to do is exit 0.
"""

import os
import re
import sys
import time
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import capture, config, importer, ledger, llm, render, store
from .serialize import _read_serialize_log

RESUME_HINT = (
    "run the same command again to resume; done sessions are skipped for free."
)

# The lines `serializer.verify_quotes` logs, routed into serialize.log by
# the handler `_run_serialize` attaches. Counted over the bytes this run
# appended, so the number is this session's own.
_DOWNGRADE_RE = re.compile(
    r"quote verification: (\d+) (?:verbatim|verified) item\(s\) downgraded"
)


def _log_path() -> Path:
    return config.log_dir() / "serialize.log"


def _log_size() -> int:
    try:
        return _log_path().stat().st_size
    except OSError:
        return 0


def _downgrades_since(offset: int) -> int:
    try:
        with _log_path().open("rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    return sum(int(m.group(1)) for m in _DOWNGRADE_RE.finditer(tail))


def _tokens_of(checkpoint) -> int | None:
    """`capture_cost.total_tokens` as the checkpoint stamped it (#1235), or
    None when the stamp is absent or the backend reported nothing."""
    if not isinstance(checkpoint, dict):
        return None
    cost = checkpoint.get("capture_cost")
    if not isinstance(cost, dict):
        return None
    total = cost.get("total_tokens")
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _print_plan(plan: importer.Plan) -> None:
    name = Path(plan.project).name or plan.project
    hosts = sorted({s.host for s in plan.sessions})
    where = f" ({'+'.join(hosts)})" if hosts else ""
    n = len(plan.sessions)
    print(
        f"import plan for {name}{where}: {n} session{'s' if n != 1 else ''}, "
        f"about {plan.calls} model call{'s' if plan.calls != 1 else ''}"
    )
    for i, s in enumerate(plan.sessions, 1):
        line = (
            f"  {i}  {s.ended[:10]}  {s.messages} msgs  {s.calls} "
            f"call{'s' if s.calls != 1 else ''}  {s.session_id}"
        )
        if s.notes:
            line += "  note: " + ", ".join(s.notes)
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
    try:
        plan = importer.plan_import(
            project,
            args.transcript,
            now=time.time(),
            log_text=_read_serialize_log(),
            live_session_id=os.environ.get("CLAUDE_CODE_SESSION_ID"),
        )
    except importer.ProjectMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_plan(plan)
    if args.plan:
        return 0
    if not plan.sessions:
        print("nothing to import")
        return 0
    bound = args.max_calls if args.max_calls is not None else plan.calls
    n = len(plan.sessions)
    imported = 0
    calls = 0
    tokens = 0
    unreported = 0
    rescued: list = []
    for i, s in enumerate(plan.sessions, 1):
        if calls + s.calls > bound:
            print(
                f"stopped before [{i}/{n}] {s.session_id}: its {s.calls} "
                f"call{'s' if s.calls != 1 else ''} would pass --max-calls "
                f"{bound} ({calls} spent so far). {imported} of {n} "
                f"imported, {n - imported} left."
            )
            print(RESUME_HINT)
            return 0
        ledger.write_import_marker(s.session_id, project, str(s.path))
        offset = _log_size()
        start = time.monotonic()
        with render.working(f"importing {s.session_id}"):
            rc = _cli._run_serialize(s.path, project, door=capture.DOOR_IMPORT)
        took = int(time.monotonic() - start)
        written = store.read_checkpoint(s.session_id) if rc == 0 else None
        if written is None:
            print(f"[{i}/{n}] error after {took}s (the error line is above)")
            print(
                f"stopped after the first failed session. {imported} of {n} "
                f"imported, {n - imported} left."
            )
            print(RESUME_HINT)
            return 1
        imported += 1
        calls += s.calls
        total = _tokens_of(written)
        if total is None:
            unreported += 1
            line = f"[{i}/{n}] wrote checkpoint (took {took}s) tokens unreported"
        else:
            tokens += total
            line = f"[{i}/{n}] wrote checkpoint (took {took}s) {total:,} tokens"
        downgraded = _downgrades_since(offset)
        if downgraded:
            line += f", {downgraded} quote{'s' if downgraded != 1 else ''} downgraded"
        if llm.fallback_used():
            rescued.append(s.session_id)
            line += " [fallback backend]"
        print(line)
    summary = (
        f"imported {imported} of {n}, {calls} call{'s' if calls != 1 else ''}, "
        f"{tokens:,} tokens reported"
    )
    if unreported:
        summary += f", {unreported} session{'s' if unreported != 1 else ''} unreported"
    if rescued:
        summary += f", {len(rescued)} via fallback backend ({', '.join(rescued)})"
    print(summary)
    return 0


def _positive(raw: str) -> int:
    import argparse

    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {raw!r}")
    if value < 1:
        raise argparse.ArgumentTypeError("--max-calls needs a count of 1 or more")
    return value


def register(sub, fmt) -> None:
    """Register `import` on the top-level subparsers."""
    p = sub.add_parser(
        "import",
        help="serialize past transcripts of one project in one bounded run; "
        "prints the plan and the model-call count before spending",
    )
    p.add_argument(
        "--project",
        help="project directory the transcripts belong to "
        "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p.add_argument(
        "--transcript",
        action="append",
        required=True,
        metavar="PATH",
        help="a transcript file to import (repeatable); each must record "
        "this project as its working directory",
    )
    p.add_argument(
        "--max-calls",
        type=_positive,
        default=None,
        metavar="N",
        help="stop before the session that would pass N model calls "
        "(default: the planned count)",
    )
    p.add_argument(
        "--plan",
        action="store_true",
        help="print the plan and exit without a model call",
    )
    p.set_defaults(func=_cmd_import)
