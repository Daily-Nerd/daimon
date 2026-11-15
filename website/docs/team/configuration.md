---
sidebar_position: 3
description: "The daimon-team.toml squad tree, the scope allowlist that decides which projects sync, and the team environment variables."
---

# Team configuration

Reference for the files and variables that control team memory. To set it up from scratch, follow [set up team memory](./setup.md). All other variables are in the [configuration reference](../reference/configuration.md).

## Project layout: the architect-authored squad tree

Checkpoints in the sidecar are grouped by **logical project**, so several repos
can share one memory pool and the same repo maps to the same pool on every
teammate's machine. The hierarchy is organizational, not forge-derived: a team
architect authors `daimon-team.toml` at the sidecar root, the squad tree with
repos mapped into it, and commits it like any other file. **Daimon only reads
this file; humans write and commit it.**

```toml
# daimon-team.toml — at the sidecar repo root, written by your team architect.
#
# One table per logical project. The key is the project's path in the squad
# tree (any depth); `repos` lists every repo that feeds that project's pool.
# ssh/https/scp spellings of the same repo are equivalent — the origin URL is
# normalized (scheme, credentials, `.git`, case) before matching.

[projects."core/cosmo/dusters/finance-1"]
repos = [
  "git@github.com:org/finance-svc.git",    # several repos → ONE shared pool
  "https://github.com/org/finance-web",
]

[projects."core/api-gateway"]
repos = ["git@github.com:org/gateway.git"]
```

On disk, a mapped repo's checkpoints land at
`projects/core/cosmo/dusters/finance-1/authors/<author-slug>/<session_id>.json`
,  every path segment is munged filesystem-safe, so a config key can never
escape the sidecar.

A session's logical project is resolved in this order:

1. **`DAIMON_TEAM_PROJECT`**, a relative path like `core/api-gateway`, set
   per machine. Explicit local intent; beats the config file.
2. **The `daimon-team.toml` mapping**, the session repo's `origin` URL is
   normalized and matched against every project's `repos`.
3. **Origin-derived fallback**, the origin URL's path without the host
   (`git@github.com:org/repo.git` → `projects/org/repo/…`). Unmapped repos
   still get portable identity, so the config file is optional and
   incremental, add mappings as the squad tree takes shape.
4. **No origin at all** (no git, no remote), the checkpoint files directly
   under `authors/<author-slug>/`, exactly as before this layout existed.

The config file is optional and can be added or changed at any time, unmapped
repos sync under their origin-derived path from day one. Remapping a repo never
orphans its earlier history: reads cover the previous location too, so
checkpoints already sitting under the old path stay visible after the move.

A broken or unparseable `daimon-team.toml` never blocks a write: the mapping
is treated as absent (resolution falls through to the origin-derived
fallback) and the parse error is surfaced as a warning in `daimon team
status`. Membership (below) is the one exception, it fails **closed**, so a
paste error can never open the remote to the whole machine.

**Legacy note:** sidecars written before this layout keep their flat
`authors/<author-slug>/` files. That era stays readable forever, reads fan in
across both layouts and there is no migration; new checkpoints simply start
landing under `projects/` once a project identity resolves.

**Reserved segment.** `authors` names the per-author directory level, so it
cannot appear as a segment of a logical project path. `daimon team init` refuses
a project whose path contains it, and the resolver drops such a path (from
`DAIMON_TEAM_PROJECT`, from the origin-derived fallback, or from a
`[projects."…"]` table, which `daimon team status` reports). Migration: rename
the offending segment in `daimon-team.toml` or `DAIMON_TEAM_PROJECT`. Files
already sitting under a path that contains `authors` are not read until the
path is renamed. Reading less is not the safe direction for tombstones: until
the rename, a value someone forgot under such a path is not suppressed on
your machine and can reappear.

## Which projects sync: the scope allowlist (default-closed)

`DAIMON_TEAM=1` is machine-global, but a synced remote only accepts
checkpoints from projects it has **granted membership**. Everything else stays
in the machine-local mirror (`<team_dir>/local/`), withheld from the remote,
never lost. Without this gate, one enabled remote would receive every project
on the machine, including personal ones.

The same allowlist gates **reads** too: `brief --team` and the recall index
only admit a synced remote's content when the scope check passes. A remote
this project was never granted to contributes nothing to this project's
briefing or search index, the files stay untouched in the sidecar clone,
they just don't enter local surfaces. The machine-local mirror
(`<team_dir>/local/`, your own withheld writes) needs no grant.

A project is in scope for a remote when any of these holds:

1. Its origin URL is listed under the sidecar's top-level `[scope]` table:

   ```toml
   [scope]
   repos = ["git@github.com:org/finance-svc.git"]
   ```

2. Its origin URL is mapped under any `[projects.*]` table, a repo the
   architect placed in the squad tree is a member, so existing mapped
   sidecars keep syncing with no new configuration.
3. `DAIMON_TEAM_PROJECT` is set on the machine, explicit local intent.

`daimon team init` seeds a fresh (empty) remote with a `daimon-team.toml`
scoping the team to the project you ran it from, so new setups need no extra
step. Joining an established remote never writes config, the architect owns
the file after birth.

**Migrating an existing sidecar** (created before scoping existed): add the
`[scope]` block above, one line per repo that should sync, commit, and
push. Until then `daimon team status` shows `scope: none, this remote
receives no checkpoints`. If foreign project trees already accumulated under
`projects/`, remove them with plain `git rm -r projects/<path>` + push
(history rewrite optional; the files remain in git history without it).

## Environment reference

The environment variables for team memory are in the [Team memory section of the configuration reference](../reference/configuration.md#team-memory), which is their one home.
