"""`daimon projects`, `slug` and the `bucket` verbs (moved out of cli/__init__.py, #1132 PR 5).

The project listing rows (also read by the MCP `projects` tool), the slug
printer and the bucket migration verb. Every name is re-exported from `cli`.
"""

import functools
import json
import os
import sys
import time

import daimon_briefing.cli as _cli

from .. import bucket_tools, buckets, config, effects_commit, render, store, view
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


def projects_listing(project_arg=None, *, own_only: bool = False) -> tuple:
    """`(rows, notes)`: one JSON-ready row per checkpoint bucket, newest
    first, and the notes of the listing (`view.projects_notes`: a bucket whose
    trust ledger cannot be read, a forget set that may be incomplete). Single
    assembler for `daimon projects --json` AND the MCP projects tool (#261) —
    two consumers, one shape. Torn buckets show with unknown fields rather
    than vanish: hiding one would read as "no such project". The topic is the
    view's call (`view.peek`): a forgotten, quarantined or unverifiable one is
    None, the same as no topic at all. `view.projects` reads a checkpoint only
    for a bucket the caller may list (#899), so a tenant-scoped home gets its
    own and never opens another. `own_only` narrows the same way without
    tenant scope: the MCP tool lists only its own bucket unless the host
    granted cross-project reads (#1223)."""
    cur_slug = store.bucket_of(_cli._resolve_project(project_arg))
    rows = []
    listed = view.projects(cur_slug, own_only=own_only)
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
    return rows, view.projects_notes(cur_slug, listed, own_only=own_only)


def _cmd_slug(args) -> int:
    """Print the checkpoint bucket name daimon derives from a project path
    (#913). No store, config, or ledger access — deliberately: a host that
    wants this name before the first write (to lay out a fresh volume, or
    start a watcher on a directory daimon has not touched yet) has no bucket
    to list, so this must answer without one, unlike `projects` below."""
    slug = store.location_slug(args.path)
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


def _stdout_is_tty() -> bool:
    """Whether names may be printed. A bucket name is the project's whole
    location, so another project's name goes to a person at a terminal, never
    into a pipe or a transcript an agent will read."""
    return sys.stdout.isatty()


def _refuse_on_tenant_scope(args, verb: str) -> int | None:
    """rc 2 (and the refusal printed) when the home is tenant-scoped, else None.

    `bucket merge` names buckets, `bucket stamp` and `bucket doctor` enumerate
    every one of them. Each is a way to reach a bucket that is not the
    caller's own, which a tenant-scoped home forbids (#899, scar 0071), so the
    refusal is unconditional and nothing is read first."""
    if not config.tenant_scoped():
        return None
    message = (f"this daimon home is tenant-scoped (DAIMON_TENANT_SCOPED): "
               f"`bucket {verb}` reaches buckets by name or enumerates them "
               f"all, and a tenant may not choose or list its scope.")
    if getattr(args, "json", False):
        print(json.dumps({"refused": message}, indent=2, ensure_ascii=False))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 2


def _refused(args, exc) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"refused": str(exc)}, indent=2, ensure_ascii=False))
    else:
        print(f"error: {exc}", file=sys.stderr)
    return 2


def _bucket_merge_lines(record: dict, *, dry_run: bool) -> list:
    """The human render of one `bucket merge` record. Pure."""
    if record["mode"] == "absent":
        return [f"nothing to merge: {record['from_slug']} is not there "
                f"(already folded, or never existed)"]
    verb = "would fold" if dry_run else "folded"
    lines = [f"{verb} {record['from_slug']} into {record['to_slug']}"]
    for name, count in sorted(record["ledgers"].items()):
        appended = "would append" if dry_run else "appended"
        lines.append(f"  {name}: {appended} {count} line(s)")
    if record.get("promoted"):
        lines.append(f"  promoted {record['promoted']} to latest in "
                     f"{record['to_slug']}")
    if record.get("pointers_evicted"):
        lines.append(f"  rotated out of the {record['to_slug']} chain: "
                     f"{', '.join(record['pointers_evicted'])}")
    if record.get("pointers_dropped"):
        lines.append(f"  {len(record['pointers_dropped'])} pointer(s) fit no "
                     f"slot and are dropped (their flat session files still "
                     f"hold the bytes): {', '.join(record['pointers_dropped'])}")
    for name in record.get("target_unreadable") or []:
        lines.append(f"  {name} in {record['to_slug']} could not be read, so "
                     f"no pointer was moved: fix or move that file, then run "
                     f"again")
    unreadable = record.get("unreadable") or []
    for name in unreadable:
        lines.append(f"  {name} could not be read, so it was not moved: fix "
                     f"or move that file, then run again")
    for name in record["leftovers"]:
        if name in unreadable or store._POINTER_RE.match(name):
            continue
        lines.append(f"  {name} could not be read as anything this verb "
                     f"moves: move it out of {record['from_slug']} to finish")
    if record.get("observed"):
        lines.append(f"  {record['observed']}: it stays, complete is false, "
                     f"run again to finish")
    if dry_run:
        lines.append("plan only, nothing was written: run again with --apply")
    return lines


def _cmd_bucket_merge(args) -> int:
    """Fold one bucket into another, once, on purpose (slice 3b).

    Dry run unless `--apply`. rc 0 finished, 1 something is left behind (the
    remedy is printed), 2 refused. See `buckets.merge` for what is refused."""
    refused = _refuse_on_tenant_scope(args, "merge")
    if refused is not None:
        return refused
    try:
        record = buckets.merge(args.from_bucket, args.into_bucket,
                               apply=args.apply, force=args.force)
    except buckets.MigrationError as exc:
        return _refused(args, exc)
    rc = 0 if record.get("complete", True) else 1
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return rc
    render.render_ledger_lines(
        _bucket_merge_lines(record, dry_run=not args.apply))
    return rc


def _counted(items: dict, listing: bool) -> list:
    """`name: count`, with the names after it only when `listing` is allowed."""
    lines = []
    for key, value in items.items():
        if not value:
            continue
        count = len(value) if isinstance(value, list) else value
        line = f"{key}: {count}"
        if listing and isinstance(value, list):
            line += " (" + ", ".join(
                " + ".join(v) if isinstance(v, list) else str(v)
                for v in value) + ")"
        lines.append(line)
    return lines


def _cmd_bucket_doctor(args) -> int:
    """Survey every bucket, read-only (slice 3b). Counts always; names, the
    candidates for `--path` and the merge suggestions only at a terminal."""
    refused = _refuse_on_tenant_scope(args, "doctor")
    if refused is not None:
        return refused
    survey = bucket_tools.doctor(path=args.path)
    listing = _stdout_is_tty()
    if args.json:
        shown = dict(survey)
        if not listing:
            shown["findings"] = {k: len(v)
                                 for k, v in survey["findings"].items()}
            shown["candidates"] = len(survey["candidates"])
            shown["suggestions"] = len(survey["suggestions"])
        print(json.dumps(shown, indent=2, ensure_ascii=False))
        return 0
    lines = [f"buckets: {survey['buckets']}"]
    found = _counted(survey["findings"], listing)
    lines += found or ["no findings"]
    if args.path is not None:
        if listing:
            for match in survey["candidates"]:
                lines.append(f"  candidate {match['bucket']}: same "
                             f"{' and '.join(match['matched'])} as {args.path}")
                lines.append(f"    if it is the same project: daimon bucket "
                             f"adopt --bucket={match['bucket']} "
                             f"--path {args.path}")
        lines.append(f"candidates: {len(survey['candidates'])} "
                     f"({survey['candidates_note']})")
    if survey["suggestions_withheld"]:
        lines.append("merge suggestions withheld for worktree-looking roots: "
                     "their live folder would create the folded bucket again")
    if survey["suggestions"]:
        if listing:
            lines.append("check which bucket is the main one before running:")
            for s in survey["suggestions"]:
                lines.append(f"  daimon bucket merge --from={s['from']} "
                             f"--into={s['into']}")
        else:
            lines.append(f"suggestions: {len(survey['suggestions'])} (shown "
                         f"on a terminal)")
    render.render_ledger_lines(lines)
    return 0


def _cmd_bucket_stamp(args) -> int:
    """Give buckets the evidence to survive a move (slice 3b). Dry run unless
    `--apply`; rc 1 when a bucket needed a person (two matches, a lost race)."""
    refused = _refuse_on_tenant_scope(args, "stamp")
    if refused is not None:
        return refused
    try:
        result = bucket_tools.stamp(scan=args.scan or (), apply=args.apply)
    except bucket_tools.StampRefused as exc:
        return _refused(args, exc)
    actions = result["actions"]
    tally: dict[str, int] = {}
    for a in actions:
        tally[a["action"]] = tally.get(a["action"], 0) + 1
    needs_a_person = sum(tally.get(k, 0) for k in
                        ("ambiguous", "raced", "unreadable"))
    listing = _stdout_is_tty()
    if args.json:
        print(json.dumps({"applied": result["applied"], "counts": tally,
                          **({"actions": actions} if listing else {})},
                         indent=2, ensure_ascii=False))
        return 1 if needs_a_person else 0
    verb = {"identity": "write identity for", "root": "stamp root of"}
    lines = []
    if not actions:
        lines.append("no buckets")
    for action, count in sorted(tally.items()):
        label = action if result["applied"] else (
            f"would {verb[action]}" if action in verb else action)
        lines.append(f"{label}: {count}")
    if listing:
        for a in actions:
            if a["action"] in ("kept", "unmatched"):
                continue
            tail = (f" {a['root']}" if a.get("root")
                    else f" ({len(a['candidates'])} repositories match)"
                    if a.get("candidates") else "")
            lines.append(f"  {a['action']} {a['bucket']}{tail}")
    if not result["applied"]:
        lines.append("plan only, nothing was written: run again with --apply")
    render.render_ledger_lines(lines)
    return 1 if needs_a_person else 0


def _adopt_lines(record: dict, *, listing: bool, dry_run: bool) -> list:
    """The human render of one `bucket adopt` record. Pure. The root the bucket
    leaves and the anchor evidence are another project's facts, so they are
    shown to a terminal only."""
    bucket, target = record["bucket"], record["to"]
    if record["action"] == "kept":
        return [f"nothing to do: {bucket} already records {target}"]
    verb = "would rebind" if dry_run else "rebound"
    lines = [f"{verb} {bucket} to {target}"]
    if listing:
        if record.get("from"):
            lines.append(f"  it recorded {record['from']}, kept in its "
                         f"identity history")
        evidence = record.get("evidence") or {}
        if evidence.get("matched"):
            lines.append(f"  evidence, not proof: same "
                         f"{' and '.join(evidence['matched'])} as that "
                         f"folder")
        if evidence.get("differs"):
            lines.append(f"  evidence, not proof: different "
                         f"{' and '.join(evidence['differs'])}")
    if dry_run:
        lines.append("plan only, nothing was written: run again with --apply")
    return lines


def _cmd_bucket_adopt(args) -> int:
    """Bind one bucket to a folder, on purpose (slice 4b). Dry run unless
    `--apply`. rc 0 done or nothing to do, 2 refused. See
    `bucket_tools.adopt` for what is refused."""
    refused = _refuse_on_tenant_scope(args, "adopt")
    if refused is not None:
        return refused
    try:
        record = bucket_tools.adopt(args.bucket, args.path, apply=args.apply,
                                    force=args.force)
    except bucket_tools.RebindRefused as exc:
        return _refused(args, exc)
    listing = _stdout_is_tty()
    if args.json:
        shown = dict(record)
        if not listing:
            shown.pop("from", None)
            shown.pop("evidence", None)
        print(json.dumps(shown, indent=2, ensure_ascii=False))
        return 0
    render.render_ledger_lines(
        _adopt_lines(record, listing=listing, dry_run=not args.apply))
    return 0


# What a remap leaves for a person: not "missing" (a project not cloned yet is
# the normal case on a fresh machine) but everything that was skipped for a
# reason a person has to look at.
_REMAP_NEEDS_A_PERSON = ("collision", "separator", "unreadable", "locked",
                         "raced")


def _remap_lines(result: dict, old: str, *, listing: bool) -> list:
    """The human render of one `bucket remap` result. Pure."""
    actions = result["actions"]
    if not actions:
        return [f"no bucket records a root under {old}"]
    applied = result["applied"]
    tally: dict[str, int] = {}
    for a in actions:
        tally[a["action"]] = tally.get(a["action"], 0) + 1
    lines = []
    for action, count in sorted(tally.items()):
        label = action
        if action == "rewrite":
            label = "rewritten" if applied else "would rewrite"
        lines.append(f"{label}: {count}")
    if listing:
        for a in actions:
            lines.append(f"  {a['action']} {a['bucket']}: {a['from']}"
                         + (f" -> {a['to']}" if a.get("to") else ""))
            if a["action"] == "collision":
                lines.append(
                    f"    that folder already belongs to {a['other']}: "
                    f"daimon bucket merge --from={a['other']} "
                    f"--into={a['bucket']}")
    if not applied:
        lines.append("plan only, nothing was written: run again with --apply")
    return lines


def _cmd_bucket_remap(args) -> int:
    """Rewrite the root of every bucket under one folder to the same place
    under another, once a folder or a username changed (slice 4b). Dry run
    unless `--apply`; rc 1 when a bucket needed a person (a collision, a lock,
    a lost race), 2 refused. See `bucket_tools.remap`."""
    refused = _refuse_on_tenant_scope(args, "remap")
    if refused is not None:
        return refused
    try:
        result = bucket_tools.remap(args.from_root, args.to_root,
                                    apply=args.apply)
    except bucket_tools.RebindRefused as exc:
        return _refused(args, exc)
    tally: dict[str, int] = {}
    for a in result["actions"]:
        tally[a["action"]] = tally.get(a["action"], 0) + 1
    rc = 1 if any(tally.get(k) for k in _REMAP_NEEDS_A_PERSON) else 0
    listing = _stdout_is_tty()
    if args.json:
        print(json.dumps({"applied": result["applied"], "counts": tally,
                          **({"actions": result["actions"]} if listing
                             else {})}, indent=2, ensure_ascii=False))
        return rc
    render.render_ledger_lines(
        _remap_lines(result, args.from_root, listing=listing))
    return rc


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

    p_bucket_merge = bucket_sub.add_parser(
        "merge",
        help="fold one bucket into another, once (a worktree or ghost bucket)",
        description="Fold the bucket named by --from into the bucket named "
                    "by --into: ledger lines the target lacks are appended, "
                    "the newest session of --from becomes the target's "
                    "latest when it is newer, and --from is removed with an "
                    "alias recorded so readers follow it. Pointers that fit "
                    "no slot are dropped and listed (the flat session files "
                    "still hold them). Refused while --from's recorded root "
                    "still exists on disk or is not recorded, unless --force. Dry run by "
                    "default. Human-only, and refused on a tenant-scoped "
                    "home.",
        epilog="Examples:\n"
               "  daimon bucket merge --from=-Users-me-repo-wt "
               "--into=-Users-me-repo\n"
               "  daimon bucket merge --from=OLD --into=MAIN --apply\n"
               "Bucket names usually start with '-', so write --from=NAME.",
    )
    p_bucket_merge.add_argument(
        "--from", dest="from_bucket", required=True, metavar="BUCKET",
        help="the bucket to fold away (its directory name under checkpoints/)")
    p_bucket_merge.add_argument(
        "--into", dest="into_bucket", required=True, metavar="BUCKET",
        help="the bucket that keeps the history (must exist)")
    p_bucket_merge.add_argument(
        "--apply", action="store_true",
        help="write the merge (default: print the plan only)")
    p_bucket_merge.add_argument(
        "--force", action="store_true",
        help="merge even though --from's recorded root still exists, or it "
             "has none (nothing then proves its project is gone)")
    p_bucket_merge.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_merge.set_defaults(func=_cmd_bucket_merge)

    p_bucket_doctor = bucket_sub.add_parser(
        "doctor",
        help="survey the buckets: dangling, unstamped, shared, empty, worktree",
        description="Read-only survey of every bucket: recorded roots that no "
                    "longer exist, buckets with no root, two buckets with one "
                    "root, empty and ghost buckets, worktree-looking roots, "
                    "buckets left by an unfinished merge, identity records "
                    "that disagree. With --path, also lists dangling buckets "
                    "whose recorded origin URL or root commit match that "
                    "folder (evidence, not proof). Bucket names are printed "
                    "only to a terminal, counts otherwise. Human-only, and "
                    "refused on a tenant-scoped home.",
        epilog="Examples:\n"
               "  daimon bucket doctor\n"
               "  daimon bucket doctor --path .\n",
    )
    p_bucket_doctor.add_argument(
        "--path", help="a project folder to look for old buckets of")
    p_bucket_doctor.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_doctor.set_defaults(func=_cmd_bucket_doctor)

    p_bucket_stamp = bucket_sub.add_parser(
        "stamp",
        help="record where each bucket's project lives, before a move",
        description="Give every stamped bucket an identity record (origin "
                    "URL without credentials, root commit, folder name) and "
                    "stamp an unstamped bucket with its folder when exactly "
                    "one repository under a --scan directory has that "
                    "bucket's name; two matches refuse. Run it on the OLD "
                    "machine, before copying the store. Dry run by default. "
                    "Signed checkpoints are never touched. Human-only, and "
                    "refused on a tenant-scoped home.",
        epilog="Examples:\n"
               "  daimon bucket stamp --scan ~/code\n"
               "  daimon bucket stamp --scan ~/code --scan ~/work --apply\n",
    )
    p_bucket_stamp.add_argument(
        "--scan", action="append", metavar="DIR",
        help="a folder to look for repositories in (repeatable)")
    p_bucket_stamp.add_argument(
        "--apply", action="store_true",
        help="write the stamps (default: print the plan only)")
    p_bucket_stamp.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_stamp.set_defaults(func=_cmd_bucket_stamp)

    p_bucket_adopt = bucket_sub.add_parser(
        "adopt",
        help="bind one bucket to the folder its project lives in now",
        description="Rewrite the recorded root of ONE bucket to a folder, "
                    "after a project moved and `remap` does not cover it. "
                    "The bucket keeps its name and every signed file; its "
                    "old root is kept in its identity history. Refused "
                    "when the folder is not a directory, when another "
                    "bucket already records it (fold that one with `bucket "
                    "merge` first), when the bucket's recorded root still "
                    "exists (a clone) unless --force, and when its identity "
                    "record cannot be read. Dry run by default. Human-only, "
                    "and refused on a tenant-scoped home.",
        epilog="Examples:\n"
               "  daimon bucket adopt --bucket=-Users-me-old --path ~/code/new\n"
               "  daimon bucket adopt --bucket=NAME --path . --apply\n"
               "Bucket names usually start with '-', so write --bucket=NAME.",
    )
    p_bucket_adopt.add_argument(
        "--bucket", required=True, metavar="BUCKET",
        help="the bucket to rebind (its directory name under checkpoints/)")
    p_bucket_adopt.add_argument(
        "--path", required=True, metavar="DIR",
        help="the folder the project lives in now (must exist)")
    p_bucket_adopt.add_argument(
        "--apply", action="store_true",
        help="write the rebind (default: print the plan only)")
    p_bucket_adopt.add_argument(
        "--force", action="store_true",
        help="rebind even though the bucket's recorded root still exists")
    p_bucket_adopt.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_adopt.set_defaults(func=_cmd_bucket_adopt)

    p_bucket_remap = bucket_sub.add_parser(
        "remap",
        help="move every bucket under one folder to the same place under another",
        description="After a folder or username change, rewrite the recorded "
                    "root of every bucket under --from to the same place "
                    "under --to. Matching stops at a separator, so /Users/a "
                    "never matches /Users/abc. A bucket whose new folder "
                    "does not exist yet (a project not cloned here) is "
                    "skipped and listed; one whose new folder already "
                    "belongs to another bucket is skipped and listed with "
                    "the `bucket merge` that folds them. Refused for the "
                    "filesystem root, a bare drive, a relative --to or a "
                    "--to inside --from. Safe to run twice. Dry run by "
                    "default. Human-only, and refused on a tenant-scoped "
                    "home.",
        epilog="Examples:\n"
               "  daimon bucket remap --from /Users/alice --to /Users/bob\n"
               "  daimon bucket remap --from C:\\Users\\a --to "
               "C:\\Users\\b --apply\n",
    )
    p_bucket_remap.add_argument(
        "--from", dest="from_root", required=True, metavar="DIR",
        help="the folder the projects used to live under")
    p_bucket_remap.add_argument(
        "--to", dest="to_root", required=True, metavar="DIR",
        help="the folder they live under now")
    p_bucket_remap.add_argument(
        "--apply", action="store_true",
        help="write the rebinds (default: print the plan only)")
    p_bucket_remap.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_remap.set_defaults(func=_cmd_bucket_remap)

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
