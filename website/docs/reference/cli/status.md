---
sidebar_position: 8
description: "Every `daimon` command in the Status group: usage, arguments and defaults, generated from the CLI."
---

# Status

Look at the store, its ledgers and the other projects.

:::note
This page is generated from the CLI's argument parsers. Do not edit it: change the flag or its help text in the code, then run `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` from `plugin/`.
:::

See also: [Troubleshooting](../../guides/troubleshooting.md), [Team memory](../../team/team.md).

## `daimon status`

checkpoint presence/age + last serialize outcome

```text
daimon status [--project PROJECT] [--json] [--suppressed]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory to check (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |
| `--suppressed` |  |  | list items withheld from the briefing as resolved (#103) |

**Examples**

```text
daimon status
daimon status --project . --json
```

## `daimon stats`

local usage + capture aggregates (#54): nothing is transmitted; sharing the output is a deliberate paste

```text
daimon stats [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |

## `daimon log`

append a freeform timeline event (zero-LLM) to this project's event log (#102)

```text
daimon log --text TEXT [--kind KIND] [--status STATUS] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--text` | `TEXT` |  | what happened (required) |
| `--kind` | `KIND` | `note` | event kind (default: note) |
| `--status` | `STATUS` |  | optional free-form status |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

## `daimon loops`

list open, briefable loop items with ids for this project (#480): the read counterpart to daimon resolve's write path

```text
daimon loops [--project PROJECT] [--stale]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--stale` |  |  | list only the carried items past the staleness budget, the set the briefing may drop |

**Examples**

```text
daimon loops
daimon loops --stale
daimon loops --project .
```

## `daimon projects`

list every project daimon has a checkpoint for

```text
daimon projects [--project PROJECT] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory the current-project mark is computed against (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

**Examples**

```text
daimon projects
daimon projects --json
```

## `daimon slug`

print the checkpoint directory name daimon derives from a project path

Print the checkpoint directory name daimon derives from a project path. Read-only: no store, config, or ledger access.

```text
daimon slug path
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `path` |  |  | project directory path to slug |

**Examples**

```text
daimon slug /Users/x/my.proj
daimon slug -- -Users-x        # '--' escapes a path starting with '-'
```

## `daimon bucket`

maintain the checkpoint bucket this project reads and writes

```text
daimon bucket <command>
```

| command | what it does |
| --- | --- |
| [`migrate`](#daimon-bucket-migrate) | move a bucket written before 0.42.0 into the one daimon reads |
| [`merge`](#daimon-bucket-merge) | fold one bucket into another, once (a worktree or ghost bucket) |
| [`doctor`](#daimon-bucket-doctor) | survey the buckets: dangling, unstamped, shared, empty, worktree |
| [`stamp`](#daimon-bucket-stamp) | record where each bucket's project lives, before a move |

### `daimon bucket migrate`

move a bucket written before 0.42.0 into the one daimon reads

Move the bucket a pre-0.42.0 daimon wrote for this project into the bucket this daimon reads. Affects a project whose path carries a symlink component, or one below a git toplevel: before 0.42.0 the library slugged the literal path. A pointer already in the current bucket is never removed or displaced. Safe to run twice: a second run over an unchanged state writes nothing new and returns the code matching the state it finds.

```text
daimon bucket migrate [--project PROJECT] [--dry-run] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | print the plan and write nothing |
| `--json` |  |  | machine-readable output |

**Examples**

```text
daimon bucket migrate
daimon bucket migrate --project /tmp/my-repo --dry-run
```

### `daimon bucket merge`

fold one bucket into another, once (a worktree or ghost bucket)

Fold the bucket named by --from into the bucket named by --into: ledger lines the target lacks are appended, the newest session of --from becomes the target's latest when it is newer, and --from is removed with an alias recorded so readers follow it. Pointers that fit no slot are dropped and listed (the flat session files still hold them). Refused while --from's recorded root still exists on disk or is not recorded, unless --force. Dry run by default. Human-only, and refused on a tenant-scoped home.

```text
daimon bucket merge --from BUCKET --into BUCKET [--apply] [--force] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--from` | `BUCKET` |  | the bucket to fold away (its directory name under checkpoints/) (required) |
| `--into` | `BUCKET` |  | the bucket that keeps the history (must exist) (required) |
| `--apply` |  |  | write the merge (default: print the plan only) |
| `--force` |  |  | merge even though --from's recorded root still exists, or it has none (nothing then proves its project is gone) |
| `--json` |  |  | machine-readable output |

**Examples**

```text
  daimon bucket merge --from=-Users-me-repo-wt --into=-Users-me-repo
  daimon bucket merge --from=OLD --into=MAIN --apply
Bucket names usually start with '-', so write --from=NAME.
```

### `daimon bucket doctor`

survey the buckets: dangling, unstamped, shared, empty, worktree

Read-only survey of every bucket: recorded roots that no longer exist, buckets with no root, two buckets with one root, empty and ghost buckets, worktree-looking roots, buckets left by an unfinished merge, identity records that disagree. With --path, also lists dangling buckets whose recorded origin URL or root commit match that folder (evidence, not proof). Bucket names are printed only to a terminal, counts otherwise. Human-only, and refused on a tenant-scoped home.

```text
daimon bucket doctor [--path PATH] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--path` | `PATH` |  | a project folder to look for old buckets of |
| `--json` |  |  | machine-readable output |

**Examples**

```text
daimon bucket doctor
daimon bucket doctor --path .
```

### `daimon bucket stamp`

record where each bucket's project lives, before a move

Give every stamped bucket an identity record (origin URL without credentials, root commit, folder name) and stamp an unstamped bucket with its folder when exactly one repository under a --scan directory has that bucket's name; two matches refuse. Run it on the OLD machine, before copying the store. Dry run by default. Signed checkpoints are never touched. Human-only, and refused on a tenant-scoped home.

```text
daimon bucket stamp [--scan DIR] [--apply] [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--scan` | `DIR` |  | a folder to look for repositories in (repeatable) |
| `--apply` |  |  | write the stamps (default: print the plan only) |
| `--json` |  |  | machine-readable output |

**Examples**

```text
daimon bucket stamp --scan ~/code
daimon bucket stamp --scan ~/code --scan ~/work --apply
```

## `daimon ledger`

look after a bucket ledger file (#1132)

```text
daimon ledger <command>
```

| command | what it does |
| --- | --- |
| [`repair`](#daimon-ledger-repair) | rejoin split rows, move torn and garbage lines to &lt;name&gt;.quarantined-lines, and re-run forget over the ledger |

**Examples**

```text
daimon ledger repair trust --dry-run
daimon ledger repair events
```

### `daimon ledger repair`

rejoin split rows, move torn and garbage lines to &lt;name&gt;.quarantined-lines, and re-run forget over the ledger

```text
daimon ledger repair name [--project PROJECT] [--dry-run]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `name` |  |  | a bucket ledger: events, refutations, amendments, requests, verification, worldcheck-outcomes, forget-hits, relations, trust, request_policy_tombstones |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | report what would change and write nothing |

## `daimon team`

shared team memory: sidecar repo init/sync/status (#113)

```text
daimon team <command>
```

| command | what it does |
| --- | --- |
| [`init`](#daimon-team-init) | clone the private team sidecar repo (empty remote OK) |
| [`sync`](#daimon-team-sync) | commit+push own checkpoints; fetch teammates' only on remote change (ls-remote gate) |
| [`status`](#daimon-team-status) | per-remote freshness, own unpushed count, authors seen |

**Examples**

```text
daimon team init git@github.com:org/team-memory.git
daimon team sync
daimon team status
```

### `daimon team init`

clone the private team sidecar repo (empty remote OK)

```text
daimon team init remote_url
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `remote_url` |  |  | git remote URL of the PRIVATE team repo |

### `daimon team sync`

commit+push own checkpoints; fetch teammates' only on remote change (ls-remote gate)

```text
daimon team sync [--project PROJECT] [--apply-forget]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | accepted for CLI symmetry; sync is currently project-agnostic (all own checkpoints sync regardless of project) |
| `--apply-forget` |  |  | also rewrite THIS machine's checkpoints under teammates' forget tombstones (#600). Requires DAIMON_TEAM_APPLY_FORGET=1; typed only: the SessionStart hook spawns a bare sync, so this can never delete your belief state unattended. Without the standing consent, refuses at rc 2 before syncing anything |

### `daimon team status`

per-remote freshness, own unpushed count, authors seen

```text
daimon team status [--json]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |
