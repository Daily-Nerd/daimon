"""The plan behind `daimon import` (#1236): which sessions, in what order, at
what cost, and why every session left out was left out.

This module decides the session list and nothing else does. It is pure over
its inputs: the project (already resolved by the CLI), whether the project
was named on the command line, the transcript paths, the clock, the
serialize log text and the live session id are all handed in, so a test can
build any plan without a host. It spends nothing: the one model-shaped
number it produces, the planned call count, comes from the serializer's own
chunk and merge arithmetic (`serializer.planned_calls`), never from a model,
and it is a plan: the run bounds itself on the calls the checkpoint stamps.

Every transcript is untrusted input and is judged once, here, at the edge:

- it must stat (a missing one is a named skip, not a refusal, since nothing
  was spent);
- it must name one session (`provenance.session_key` for its host: the
  stem, or Kimi's directory segment), and one session must not be named by
  two different files (a refusal);
- it must belong to the project (`check_project`: a recorded cwd resolves
  to `--project`; with none recorded, the path lies under the project's
  Claude Code root, or the host records no project and `--project` was
  typed; anything else refuses the whole run);
- it must not be done already (`ledger.has_checkpoint`, or any success line
  in the log: invariant 3, a resume never pays twice);
- it must not be the live session (the id in CLAUDE_CODE_SESSION_ID, or a
  file modified inside LIVE_WINDOW_SECONDS);
- it must clear the too-short gate the capture itself applies.

What survives is ordered oldest first by its session-end stamp, so carry
chains forward and `latest` ends on the newest import (design 4(a)). A
session that would still land behind the bucket's live latest is named in
the plan as history only: the store keeps `latest` where it is and carries
nothing for it (slice 1), and the user is told before spending.

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

HISTORY_NOTE = "older than the latest capture, history only, nothing carried"


class ImportRefused(Exception):
    """The run is refused before any spend (design section 6). The message
    is the refusal line the CLI prints."""


class ProjectMismatch(ImportRefused):
    """A given transcript is not this project's."""


class Planned(NamedTuple):
    """One session the run will serialize, in run order."""

    session_id: str  # `provenance.session_key`: what `_run_serialize` keys on
    path: Path
    ended: str  # `capture.session_end_stamp`, the run's sort key
    messages: int  # `serializer.conversation_message_count`
    calls: int  # `serializer.planned_calls`, a plan not a spend
    notes: tuple  # transcript notes, the history note, the project source
    host: str  # `provenance.infer_host`
    history: bool  # lands behind the bucket's live latest (M1)


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


def import_done(session_key: str, captured: frozenset) -> str | None:
    """Why the import skips this session at zero cost, or None when it is
    not done. A checkpoint on disk counts whatever prompt version wrote it
    (invariant 3); so does any success line in serialize.log, which
    outlives a checkpoint the store pruned (design 4(d), widened to live
    captures). `ledger.has_checkpoint` is the reader the heal classifier
    uses, so heal and the import agree on what a captured session is."""
    if ledger.has_checkpoint(session_key):
        return "already has a checkpoint"
    if session_key in captured:
        return "already captured (serialize.log)"
    return None


def _under(path: Path, root: Path) -> bool:
    # os.path.realpath never raises (non-strict), unlike Path.resolve on a
    # symlink loop, so the containment test needs no guard.
    return Path(os.path.realpath(path)).is_relative_to(Path(os.path.realpath(root)))


def check_project(
    path: Path,
    project: str,
    claude_projects: Path,
    *,
    host: str,
    explicit_project: bool,
) -> str | None:
    """Refuse a transcript that is not `project`'s (design section 6), or
    return the note the plan prints for how the project was decided (None
    when the transcript itself says so).

    The rule, in order. A recorded working directory (`transcript.
    recorded_cwd`) is resolved by the one project resolver the CLI itself
    used for `--project` and must land on the same directory. A transcript
    that records none is accepted in two cases: its path lies under the
    project's Claude Code root, `<claude_projects>/<slug>/`, the one
    per-project host root that exists (the slug is lossy, which is why this
    is a fallback and never the first check); or `infer_host` places it
    under a registered root of a host that records no project
    (`provenance.HOSTS_RECORDING_NO_PROJECT`: Windsurf, and Kimi until a cwd
    field is measured) AND the project was typed on the command line, not
    taken from DAIMON_PROJECT_DIR or the working directory. The bucket is
    then never inferred: the user named it, and the plan repeats where it
    came from before any spend. Everything else is refused: a markdown
    export, a file outside every host root, a no-project host with no
    `--project` typed."""
    cwd = transcript.recorded_cwd(path)
    if cwd is not None:
        owner = config.resolve_project_dir(cwd, allow_slug=False)
        if owner != project:
            raise ProjectMismatch(
                f"refused: transcript belongs to {owner}, not {project} ({path})"
            )
        return None
    root = claude_projects / (store.project_slug(project) or "")
    if _under(path, root):
        return None
    if host in provenance.HOSTS_RECORDING_NO_PROJECT:
        if explicit_project:
            return f"project from --project ({host} records none)"
        raise ProjectMismatch(
            f"refused: {path} records no project directory ({host} never "
            f"does); name the project with --project on the command line"
        )
    raise ProjectMismatch(
        f"refused: {path} records no project directory and is not under "
        f"{root}; only a transcript of {project} can be imported into it"
    )


def plan_import(
    project: str,
    paths,
    *,
    now: float,
    log_text: str,
    live_session_id: str | None = None,
    claude_projects: Path | None = None,
    explicit_project: bool = False,
) -> Plan:
    """Build the plan for the given transcripts. Pure over its arguments
    apart from reading the transcript files and the checkpoint store, both
    through their existing readers. Raises `ImportRefused` for the first
    transcript that is not the project's or that names a session another
    file already named; everything else is a named skip."""
    roots = (
        claude_projects if claude_projects is not None else config.claude_projects_dir()
    )
    captured = ledger.captured_sessions(log_text)
    latest = store.read_latest_body(
        project, route=store.Route.OWN, admit=store.Admit.ANY
    )
    planned: list = []
    skipped: list = []
    named: dict = {}
    for raw in paths:
        path = Path(raw).expanduser()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            skipped.append(Skipped(path.stem, path, "transcript missing"))
            continue
        host = provenance.infer_host(path, claude_projects=roots)[0]
        sid = provenance.session_key(path, host=host)
        real = os.path.realpath(path)
        if sid in named:
            if named[sid] == real:
                continue  # the same file given twice plans once
            raise ImportRefused(
                f"refused: two transcripts name session {sid}: {named[sid]} and {path}"
            )
        named[sid] = real
        source_note = check_project(
            path, project, roots, host=host, explicit_project=explicit_project
        )
        done = import_done(ledger._session_key(sid), captured)
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
        ended = capture.session_end_stamp(path)
        # The same rule the pointer guard and carry apply on write (slice 1):
        # a session that ends before the bucket's latest keeps its own file
        # and nothing else, and the user hears it before paying.
        history = store.regresses_latest(latest, {"created": ended})
        notes = list(detailed["notes"])
        if history:
            notes.append(HISTORY_NOTE)
        if source_note:
            notes.append(source_note)
        planned.append(
            Planned(
                session_id=sid,
                path=path,
                ended=ended,
                messages=serializer.conversation_message_count(messages),
                calls=serializer.planned_calls(messages),
                notes=tuple(notes),
                host=host,
                history=history,
            )
        )
    planned.sort(key=lambda s: (s.ended, s.session_id))
    return Plan(project, tuple(planned), tuple(skipped))
