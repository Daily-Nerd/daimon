"""The plan behind `daimon import` (#1236): which sessions, in what order, at
what cost, and why every session left out was left out.

This module decides the session list and nothing else does. It is pure over
its inputs: the project (already resolved by the CLI), the transcript paths,
the clock, the serialize log text and the live session id are all handed in,
so a test can build any plan without a host. It spends nothing: the one
model-shaped number it produces, the call count, comes from the serializer's
own chunk arithmetic (`serializer.planned_calls`), never from a model.

Every transcript is untrusted input and is judged once, here, at the edge:

- it must exist (a missing one is a named skip, not a refusal, since nothing
  was spent);
- it must belong to the project (`recorded_cwd` resolves to `--project`;
  with no recorded directory the path must lie under the project's Claude
  Code root, `<claude projects>/<slug>/`, or the plan refuses the whole run);
- it must not be done already (`ledger.has_checkpoint`, or an ` [import]`
  success line in the log: invariant 3, a resume never pays twice);
- it must not be the live session (the id in CLAUDE_CODE_SESSION_ID, or a
  file modified inside LIVE_WINDOW_SECONDS);
- it must clear the too-short gate the capture itself applies.

What survives is ordered oldest first by its session-end stamp, so carry
chains forward and `latest` ends on the newest import (design 4(a)).
Discovery (which transcripts to consider when none are given) is the next
slice and lands beside `plan_import` here, not in the CLI.
"""

import os
from pathlib import Path
from typing import NamedTuple

from . import capture, config, ledger, provenance, serializer, store, transcript

# A transcript written this recently may belong to a session still running:
# its host has not fired SessionEnd yet, and importing it now would race the
# live capture for the same session id (design 4(a)).
LIVE_WINDOW_SECONDS = 600


class ProjectMismatch(Exception):
    """A given transcript is not this project's (design section 6). Raised
    before any spend; the message is the refusal line the CLI prints."""


class Planned(NamedTuple):
    """One session the run will serialize, in run order."""

    session_id: str  # the stem `_run_serialize` keys the checkpoint on
    path: Path
    ended: str  # `capture.session_end_stamp`, the run's sort key
    messages: int  # `serializer.conversation_message_count`
    calls: int  # `serializer.planned_calls`
    notes: tuple  # transcript notes, e.g. compact-summary-only-context
    host: str  # `provenance.infer_host`


class Skipped(NamedTuple):
    session_id: str
    path: Path
    reason: str


class Plan(NamedTuple):
    project: str
    sessions: tuple  # Planned, oldest first
    skipped: tuple  # Skipped, in the order the paths were given

    @property
    def calls(self) -> int:
        return sum(s.calls for s in self.sessions)


def import_done(session_key: str, imported: frozenset) -> str | None:
    """Why the import skips this session at zero cost, or None when it is
    not done. A checkpoint on disk counts whatever prompt version wrote it
    (invariant 3); so does an ` [import]` success line, which outlives a
    checkpoint the store pruned (design 4(d)). `ledger.has_checkpoint` is
    the same reader the heal classifier uses, so heal and the import agree
    on what a captured session is."""
    if ledger.has_checkpoint(session_key):
        return "already has a checkpoint"
    if session_key in imported:
        return "already imported (serialize.log)"
    return None


def _under(path: Path, root: Path) -> bool:
    # os.path.realpath never raises (non-strict), unlike Path.resolve on a
    # symlink loop, so the containment test needs no guard.
    return Path(os.path.realpath(path)).is_relative_to(Path(os.path.realpath(root)))


def check_project(path: Path, project: str, claude_projects: Path) -> None:
    """Refuse a transcript that is not `project`'s (design section 6).

    The rule, in order: a recorded working directory (`transcript.
    recorded_cwd`) is resolved by the one project resolver the CLI itself
    used for `--project` and must land on the same directory. A transcript
    that records none is accepted only when its path lies under the
    project's Claude Code root, `<claude_projects>/<slug>/`, the one
    per-project host root that exists; the slug is lossy, which is why it is
    the fallback and never the first check. Anything else is refused: a
    Windsurf transcript or a markdown export carries no project, and filing
    it by the caller's word alone is what `daimon serialize --project` is
    for, one file at a time, by hand."""
    cwd = transcript.recorded_cwd(path)
    if cwd is not None:
        owner = config.resolve_project_dir(cwd, allow_slug=False)
        if owner != project:
            raise ProjectMismatch(
                f"refused: transcript belongs to {owner}, not {project} ({path})"
            )
        return
    root = claude_projects / (store.project_slug(project) or "")
    if not _under(path, root):
        raise ProjectMismatch(
            f"refused: {path} records no project directory and is not "
            f"under {root}; only a transcript of {project} can be "
            f"imported into it"
        )


def plan_import(
    project: str,
    paths,
    *,
    now: float,
    log_text: str,
    live_session_id: str | None = None,
    claude_projects: Path | None = None,
) -> Plan:
    """Build the plan for the given transcripts. Pure over its arguments
    apart from reading the transcript files and the checkpoint store, both
    through their existing readers. Raises `ProjectMismatch` for the first
    transcript that is not the project's; everything else is a named skip."""
    roots = (
        claude_projects if claude_projects is not None else config.claude_projects_dir()
    )
    imported = ledger.imported_sessions(log_text)
    planned: list = []
    skipped: list = []
    seen: set = set()
    for raw in paths:
        path = Path(raw).expanduser()
        sid = path.stem
        if path in seen:
            continue
        seen.add(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            skipped.append(Skipped(sid, path, "transcript missing"))
            continue
        check_project(path, project, roots)
        done = import_done(ledger._session_key(sid), imported)
        if done is not None:
            skipped.append(Skipped(sid, path, done))
            continue
        if live_session_id and sid == live_session_id:
            skipped.append(
                Skipped(sid, path, "the live session (CLAUDE_CODE_SESSION_ID)")
            )
            continue
        if now - mtime < LIVE_WINDOW_SECONDS:
            skipped.append(
                Skipped(sid, path, "modified in the last 10 minutes, may be live")
            )
            continue
        try:
            detailed = transcript.from_file_detailed(path)
        except OSError:
            # Stat succeeded, the read did not: permissions, or a file gone
            # between the two. Named, and nothing was spent.
            skipped.append(Skipped(sid, path, "transcript unreadable"))
            continue
        messages = detailed["messages"]
        try:
            serializer.require_enough_messages(messages)
        except serializer.TooShortError as exc:
            skipped.append(Skipped(sid, path, str(exc)))
            continue
        planned.append(
            Planned(
                session_id=sid,
                path=path,
                ended=capture.session_end_stamp(path),
                messages=serializer.conversation_message_count(messages),
                calls=serializer.planned_calls(messages),
                notes=tuple(detailed["notes"]),
                host=provenance.infer_host(path, claude_projects=roots)[0],
            )
        )
    planned.sort(key=lambda s: (s.ended, s.session_id))
    return Plan(project, tuple(planned), tuple(skipped))
