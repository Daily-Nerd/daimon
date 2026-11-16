---
sidebar_position: 6
description: "What to run after upgrading daimon: refresh hooks and skills, re-arm checks, confirm with status, and the one-time bucket move for pre-0.42.0 stores."
---

# Upgrade daimon

```sh
uv tool upgrade daimon-briefing
```

The installed hook scripts and skill files are copies of content that moves with the CLI, so refresh them after an upgrade.

1. Refresh the hook scripts on every host that has them installed: `daimon hooks install <host>`. With no host, daimon detects what this machine runs. A host served by the daimon plugin needs nothing.
2. Check the skills with `daimon skill status`. It reports each installed skill as `CURRENT`, `STALE`, `MISSING` and so on, and exits non-zero on drift. Fix drift with `daimon skill install`.
3. If you use ruling checks, `daimon hooks install` and `daimon check sync` rebuild the manifest a host reads. `daimon check sync --check` audits it and writes nothing.
4. Run `daimon status`. A `legacy:` line means a bucket written under the pre-0.42.0 naming rule exists for this project, and the section below moves it.

Read the release's entry in `CHANGELOG.md` before you move. daimon is pre-1.0, so a breaking change ships as a minor version and is listed under `BREAKING CHANGES`.

## Upgrading across 0.42.0


Before 0.42.0 the library named a project's bucket from the literal path it
was handed. Since 0.42.0 every entry point resolves the path first: made
absolute, symlinks collapsed, then normalized to the git toplevel. Two path
shapes therefore name a different bucket than they used to:

- a path with a **symlink anywhere in it**, on macOS that includes anything
  reached through `/tmp`, and any mount point that is itself a link;
- a path **below a git toplevel**, when the write came through the library
  rather than the command line.

The CLI already resolved both of these before 0.42.0, so a bucket written by
running `daimon` in a terminal is unaffected. A bucket written by a host
process that called the library from such a path sits under the old name, and
nothing reads it.

`daimon status` says so. Whenever a bucket written under the old rule exists
for the path you are standing in, the health block carries a `legacy:` line
naming it. Move it once:

```sh
daimon bucket migrate                                # this project
daimon bucket migrate --project /path/to/project
daimon bucket migrate --project /path/to/project --dry-run
```

The move renames the old directory when the new one does not exist yet, and
merges otherwise. A merge appends every ledger line the new bucket does not
already hold, and adds the old bucket's pointers to the new bucket's chain.
Pointers already in the new bucket are never removed and never displaced: they
belong to the live project. Old pointers go into whatever slots
`DAIMON_CHECKPOINT_HISTORY` leaves free, newest first, except that an old copy
of a session the new bucket already points at simply replaces that one slot
when it is the newer of the two. Anything else, an old pointer with no free
slot or a file daimon does not write, is left exactly where it is and named in
the report with what to do about it.

Running it twice is safe: a second run over an unchanged state writes nothing
new and returns the code that matches the state it finds. `--dry-run` prints
the same plan and writes nothing.

Exit 0 means the move finished and the old bucket is gone. Exit 1 means it did
not, and stdout names each thing left with its own remedy: raise
`DAIMON_CHECKPOINT_HISTORY` to the number it names and run again for pointers
with no free slot; fix or move a file that could not be read; move a file
daimon does not write out of the old bucket. A partial move does not count as
migrated, so the old bucket's rows are not yet read as this project's history.
If you finish the job yourself by clearing the old directory, the next run
records that and the migration is complete.

Exit 2 is a refusal. A `--project` containing a `..` component is refused
because the old rule collapses `..` before following a symlink and the current
one follows the symlink first, so the two name different directories; a bucket
written before 0.42.0 was written under the literal slug of the collapsed path,
so pass that path. On a tenant-scoped home (`DAIMON_TENANT_SCOPED`) an explicit
`--project` is refused outright and the project comes from
`DAIMON_PROJECT_DIR`, else the working directory: a migration writes a lasting
alias between two buckets, and choosing which two is a scope choice. The same rule covers every verb: on a tenant-scoped home a `--project` that names another bucket is refused, whatever the verb.

Every completed move appends one line to
`~/.daimon/checkpoints/migrations.jsonl`. That file is what keeps `daimon
recall` and the request inbox finding history under the old bucket name once
the directory is gone: per-session checkpoint files keep the name they were
stamped with, because their receipts bind their exact bytes. Once a bucket
has absorbed another, `daimon status` shows a `migrated: from <old bucket> on
<date>` line in place of the warning.
