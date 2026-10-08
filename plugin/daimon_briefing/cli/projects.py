"""`daimon projects`, `slug` and `bucket migrate` (moved out of cli/__init__.py, #1132 PR 5).

The project listing rows (also read by the MCP `projects` tool), the slug
printer and the bucket migration verb. Every name is re-exported from `cli`.
"""

import functools
import json
import os
import sys
import time

import daimon_briefing.cli as _cli

from .. import buckets, config, effects_commit, render, store, view
from ..effects import Effects
from ..display import one_line
from ..ledger import _format_age


# ---- projects: cross-project bucket list (#243) ----


_TOPIC_TEASER_CHARS = 60


def _topic_teaser(topic) -> str:
    """The topic on one line, at most `_TOPIC_TEASER_CHARS` wide. Kept off
    `display.shorten`: this cut is `[:CHARS - 1]` with NO rstrip and fires
    only above CHARS, a contract `shorten(hard=True)` cannot reproduce byte
    for byte."""
    flat = one_line(topic)
    if len(flat) > _TOPIC_TEASER_CHARS:
        flat = flat[:_TOPIC_TEASER_CHARS - 1] + "…"
    return flat


def projects_rows(project_arg=None) -> list:
    """`projects_listing(project_arg)[0]`: the rows, for the callers that have
    no use for the notes. See `projects_listing`."""
    return projects_listing(project_arg)[0]


def projects_listing(project_arg=None) -> tuple:
    """`(rows, notes)`: one JSON-ready row per checkpoint bucket, newest
    first, and the notes of the listing (`view.projects_notes`: a bucket whose
    trust ledger cannot be read, a forget set that may be incomplete). Single
    assembler for `daimon projects --json` AND the MCP projects tool (#261) —
    two consumers, one shape. Torn buckets show with unknown fields rather
    than vanish: hiding one would read as "no such project". The topic is the
    view's call (`view.peek`): a forgotten, quarantined or unverifiable one is
    None, the same as no topic at all. `view.projects` reads a checkpoint only
    for a bucket the caller may list (#899), so a tenant-scoped home gets its
    own and never opens another."""
    cur_slug = store.project_slug(_cli._resolve_project(project_arg))
    rows = []
    listed = view.projects(cur_slug)
    for b in listed:
        created = b.created
        rows.append({
            "slug": b.slug,
            # #672 write-time stamp; None when the bucket predates it — never
            # a slug-derived guess, the flattening is not invertible.
            "name": b.name if isinstance(b.name, str) and b.name else None,
            "session_id": b.session_id,
            "created": created if isinstance(created, str) else None,
            "git_branch": b.git_branch,
            "topic": b.peek.topic,
            "current": b.slug == cur_slug,
            # display sort key only, never emitted: created stamp when the
            # pointer has one, pointer mtime for torn/stampless buckets
            "_epoch": store._created_epoch(created) or b.mtime,
        })
    rows.sort(key=lambda r: r["_epoch"], reverse=True)
    for r in rows:
        del r["_epoch"]
    return rows, view.projects_notes(cur_slug, listed)


def _cmd_slug(args) -> int:
    """Print the checkpoint bucket name daimon derives from a project path
    (#913). No store, config, or ledger access — deliberately: a host that
    wants this name before the first write (to lay out a fresh volume, or
    start a watcher on a directory daimon has not touched yet) has no bucket
    to list, so this must answer without one, unlike `projects` below."""
    slug = store.project_slug(args.path)
    if not slug:
        print("error: path must not be empty or whitespace-only", file=sys.stderr)
        return 2
    print(slug)
    return 0


def _raw_project(arg) -> str:
    """The project value BEFORE resolution — the exact string a pre-0.42.0
    write would have slugged (#963).

    `_resolve_project` above collapses symlinks and walks to the git toplevel,
    which is precisely the information the legacy bucket rule needs and the
    resolved rule discards. Every surface that reports on a legacy bucket
    reads this, and every surface that ROUTES still reads `_resolve_project`:
    the two are deliberately not interchangeable. The fallback chain is the
    same one `_resolve_project` uses, so both halves answer for one project.
    """
    return arg or config.project_dir() or os.getcwd()


def _migrate_command(project_path) -> str:
    """The runnable form of "migrate this bucket", for whichever mode the
    home is in. Under DAIMON_TENANT_SCOPED an explicit --project is refused at
    rc 2, so printing it hands the reader a command that cannot work."""
    if config.tenant_scoped():
        return ("run daimon bucket migrate with DAIMON_PROJECT_DIR set to "
                f"{project_path}")
    return f"run daimon bucket migrate --project {project_path}"


def _cmd_bucket_migrate(args) -> int:
    """Move this project's pre-0.42.0 bucket into the one daimon reads (#963).

    `--project` is a PATH and is absolutized, never treated as a bucket name,
    so it can never be a bare bucket slug (scar 0071).

    That alone is NOT enough, and the earlier version of this docstring said
    it was. The two slug rules differ in WHEN they resolve: the legacy rule
    collapses `..` lexically before touching a symlink, the current one
    resolves the symlink first. A path combining both therefore names two
    different DIRECTORIES, and a migration would move a bucket the caller has
    no claim on. `buckets.migrate` refuses any `..` component outright, which
    is what makes the sentence above true; the refusal arrives here as a
    MigrationError and leaves as rc 2, the same code every other refusal on
    this surface uses.

    rc 1 is a PARTIAL merge: something was left behind, unreadable or too big
    for the pointer chain. The caller learns that from the exit code without
    parsing the receipt.

    On a TENANT-SCOPED home an explicit `--project` is refused outright, the
    way `--slug` and `--all-projects` already are (#899). The path reach is
    not what is new: `--project` could always name another directory. What
    this verb adds is a PERMANENT row in the global migrations file, which
    `recall.rebuild` and `requests.recipient_join` then honor for whichever
    bucket it names. A caller who may not choose a read scope must not be
    able to mint a durable alias between two of them, so the project comes
    from the host (DAIMON_PROJECT_DIR, else cwd) and from nowhere else."""
    if config.tenant_scoped() and args.project:
        message = (
            "this daimon home is tenant-scoped (DAIMON_TENANT_SCOPED): the "
            "project is host-set, so `bucket migrate` takes it from "
            "DAIMON_PROJECT_DIR (else the working directory) and refuses an "
            "explicit --project. A migration writes a lasting alias between "
            "two buckets, which is a scope choice.")
        if args.json:
            print(json.dumps({"refused": message}, indent=2,
                             ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return 2
    raw = _raw_project(args.project)
    try:
        record = buckets.migrate(raw, dry_run=args.dry_run)
    except buckets.MigrationError as exc:
        # Machine callers get the refusal in the format they asked for; they
        # must never have to parse stderr to learn the verb said no.
        if args.json:
            print(json.dumps({"refused": str(exc)}, indent=2,
                             ensure_ascii=False))
        else:
            print(f"bucket not migrated: {exc}", file=sys.stderr)
        return 2
    rc = 0 if record.get("complete", True) else 1
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return rc
    lines = _bucket_migrate_lines(record, raw, dry_run=args.dry_run)
    render.render_ledger_lines(lines)
    return rc


def _bucket_migrate_lines(record: dict, raw: str, *, dry_run: bool) -> list:
    """The human render of one migration record. Pure, so the wording is
    testable without a filesystem."""
    mode = record["mode"]
    if mode == "unknown":
        return [f"nothing to migrate: no project resolves from {raw}"]
    if mode == "stable":
        return [f"nothing to migrate: {record['to_slug']} is stable under "
                f"both rules"]
    if mode == "absent":
        return [f"nothing to migrate: no legacy bucket "
                f"{record['from_slug']} for {raw}"]
    verb = "would move" if dry_run else "moved"
    lines = [f"{verb} {record['from_slug']} into {record['to_slug']} "
             f"({mode})"]
    for name, count in sorted(record["ledgers"].items()):
        appended = "would append" if dry_run else "appended"
        lines.append(f"  {name}: {appended} {count} line(s)")
    if record["pointers"]:
        landed = "would move" if dry_run else "moved"
        lines.append(f"  pointers: {landed} {record['pointers']} into the "
                     f"chain")
    # Every remaining line names a CONCRETE remedy for one thing. The earlier
    # wording said "remove or fix by hand" for all of them, which invited
    # deleting a legacy bucket outright and left a migration nothing could
    # ever finish.
    stranded = record.get("stranded_pointers") or []
    if stranded:
        # Arithmetic on the slots the target ACTUALLY holds, which the record
        # carries. Deriving it from DAIMON_CHECKPOINT_HISTORY assumes the
        # target occupies exactly that many, and a bucket written while the
        # knob was higher holds more: the message then names a value that
        # strands the same pointer again.
        need = record.get("target_slots", 0) + len(stranded)
        lines.append(
            f"  {len(stranded)} pointer(s) found no free slot and are still "
            f"in {record['from_slug']}: {', '.join(stranded)}")
        lines.append(f"  raise DAIMON_CHECKPOINT_HISTORY to at least {need} "
                     f"and run again")
    for name in record.get("target_unreadable") or []:
        lines.append(f"  {name} in {record['to_slug']} could not be read, so "
                     f"no pointer was moved: fix or move that file, then run "
                     f"again")
    unreadable = record.get("unreadable") or []
    for name in unreadable:
        lines.append(f"  {name} could not be read, so it was not moved: fix "
                     f"or move that file, then run again")
    for name in record["leftovers"]:
        if name in unreadable:
            continue  # already named above, with its remedy
        if stranded and store._POINTER_RE.match(name):
            continue  # named on the stranded line above, by session
        # Never a claim about who wrote it: a dangling symlink named
        # `prev-2.json` carries daimon's own naming, and an authorship claim
        # here is one nobody can check. What is true is that this verb could
        # not read it as anything it knows how to move.
        lines.append(f"  {name} could not be read as anything this verb "
                     f"moves: move it out of {record['from_slug']} to finish")
    return lines


@effects_commit.committing
def _cmd_projects(args, fx) -> int:
    """Read-only orientation for context switching — the crossing itself
    stays explicit (`brief --slug` / `recall --slug`), the #94/#95 lesson."""
    fx.add(Effects(usage=("projects",)))
    try:
        rows, notes = projects_listing(getattr(args, "project", None))
    except Exception as exc:  # noqa: BLE001 — reported, never listed around
        print("error: the projects could not be listed "
              f"({type(exc).__name__}); nothing was rendered", file=sys.stderr)
        return 2
    if args.json:
        # stdout stays the JSON array; every note goes to stderr.
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        for note in notes:
            print(note, file=sys.stderr)
        return 0
    if not rows:
        render.render_recall_lines(
            ["no project buckets yet — the first serialized session creates one"])
        for note in notes:
            print(note)
        return 0
    now = time.time()
    display = []
    for r in rows:
        epoch = store._created_epoch(r["created"])
        age = f"{_format_age(now - epoch)} ago" if epoch else "?"
        topic = _topic_teaser(r["topic"])
        display.append({
            "mark": "*" if r["current"] else " ",
            "name": r["name"] or "—",
            "slug": r["slug"], "age": age,
            "branch": r["git_branch"] or "—", "topic": topic or "?",
        })
    render.render_projects(display)
    for note in notes:
        print(note)
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_projects = sub.add_parser(
        "projects", help="list every project daimon has a checkpoint for",
        epilog="Examples:\n  daimon projects\n  daimon projects --json\n",
    )
    p_projects.add_argument(
        "--project",
        help="project directory the current-project mark is computed against "
             "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_projects.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_projects.set_defaults(func=_cmd_projects)

    p_bucket = sub.add_parser(
        "bucket",
        help="maintain the checkpoint bucket this project reads and writes",
    )
    bucket_sub = p_bucket.add_subparsers(dest="bucket_cmd", required=True)
    bucket_sub.add_parser = functools.partial(  # type: ignore[method-assign]
        bucket_sub.add_parser, formatter_class=fmt)

    p_bucket_migrate = bucket_sub.add_parser(
        "migrate",
        help="move a bucket written before 0.42.0 into the one daimon reads",
        description="Move the bucket a pre-0.42.0 daimon wrote for this "
                    "project into the bucket this daimon reads. Affects a "
                    "project whose path carries a symlink component, or one "
                    "below a git toplevel: before 0.42.0 the library slugged "
                    "the literal path. A pointer already in the current "
                    "bucket is never removed or displaced. Safe to run twice: "
                    "a second run over an unchanged state writes nothing new "
                    "and returns the code matching the state it finds.",
        epilog="Examples:\n"
               "  daimon bucket migrate\n"
               "  daimon bucket migrate --project /tmp/my-repo --dry-run\n",
    )
    p_bucket_migrate.add_argument(
        "--project",
        help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_bucket_migrate.add_argument(
        "--dry-run", action="store_true",
        help="print the plan and write nothing")
    p_bucket_migrate.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_migrate.set_defaults(func=_cmd_bucket_migrate)

    p_slug = sub.add_parser(
        "slug",
        help="print the checkpoint directory name daimon derives from a project path",
        description="Print the checkpoint directory name daimon derives from "
                     "a project path. Read-only: no store, config, or ledger "
                     "access.",
        epilog="Examples:\n"
               "  daimon slug /Users/x/my.proj\n"
               "  daimon slug -- -Users-x        # '--' escapes a path starting with '-'\n",
    )
    p_slug.add_argument("path", help="project directory path to slug")
    p_slug.set_defaults(func=_cmd_slug)
