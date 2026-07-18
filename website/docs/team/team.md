# Team memory

Team memory mirrors each of your session checkpoints — immutable, one file per
author — through a shared git sidecar repo, so a whole team's memory converges
without merging anyone's notes into anyone else's. Once it's enabled, teammates'
active topics and decisions show up attributed (never blended into your own) in
`daimon brief --team`, and their history is searchable alongside yours in
`daimon recall`.

## The shared repo is the privacy boundary — read this first

**The sidecar's access control IS the membership and privacy boundary. Whoever
can read that git repo can read everything every member's sessions produced, so
the remote MUST be a private repository.** There is no second gate. When team
memory is on, whatever a session discussed is mirrored to the shared repo
*verbatim*, after one pass of shape-based secret redaction.

That redaction is deliberately narrow. It catches secrets with a concrete,
recognizable shape — assignment-style `api_key=…` / `token:…` pairs, PEM key and
certificate blocks, `Bearer` headers, `credential://user:pass@host` URLs, and
fixed-prefix vendor tokens (AWS `AKIA…`, Stripe `sk_live_…`, GitHub `ghp_…`,
GitLab, Slack, Google, OpenAI keys, JWTs). Each match is replaced with a visible
`[redacted:<kind>]` marker. It does **not** understand meaning: free-text
confidential prose — a customer name, an unreleased plan, an internal URL written
as ordinary words — is not a secret shape and syncs through untouched. Treat the
shared repo as if every member can read every word, because they can.

Author identity is declared, not authenticated. Any member of the repo can write
under any author name; daimon cross-checks the name stamped in each arriving file
against the git author who committed it and surfaces a mismatch as a warning, but
it never blocks the write. The repo's access control is what keeps outsiders out
— the author label only tells you who a file *claims* to be from.

## Setup (two people)

Do this once per shared repo, then once on each machine.

**1. Create an empty private git repo** on your host of choice (GitHub, GitLab,
self-hosted — anything git can clone over SSH or HTTPS). Leave it empty; the
first `daimon team init` seeds it. Make sure it is **private** and that every
teammate has push access.

**2. On each machine**, enable team memory and set your author name. Put these in
`~/.daimon/env` (loaded by daimon) or export them in your shell:

```sh
DAIMON_TEAM=1
DAIMON_AUTHOR="Ada Lovelace"   # optional — see below
```

`DAIMON_AUTHOR` is optional. When it's unset, daimon falls back to
`git config user.name`, then to your OS username, and finally to `unknown`. Set
it explicitly if your git identity isn't the name you want teammates to see.

**3. On each machine**, clone the sidecar:

```sh
daimon team init git@github.com:your-org/team-memory.git
```

An empty remote is fine — the first machine to init seeds a root commit and
pushes it. Running init a second time against a directory that already exists is
an error, not a re-clone.

**4. Verify** on each machine:

```sh
daimon team status
```

You should see the remote listed with its freshness and the authors seen so far.

## What happens, and when

At the start of every session, daimon fires `daimon team sync` detached in the
background. It never blocks your briefing and never fails your session — if
anything goes wrong the session proceeds exactly as if team memory were off. The
spawn is gated on a team remote actually existing, so machines that never ran
`daimon team init` pay only a directory scan.

A sync pass does three things, in order:

- **Commits and pushes your own author directory only.** It stages and commits
  new files under `authors/<your-author-slug>/`, nothing else — never another
  author's files, never anything you happened to leave staged in the sidecar.
- **Fetches teammates' updates**, but only when the remote actually changed. A
  refs-only `ls-remote` probe compares hashes first; objects transfer only on a
  mismatch, so a no-op sync costs one lightweight network round-trip.
- Leaves everything else alone.

Only immutable per-author checkpoint files ever sync. On disk they live under
`~/.daimon/team/<remote-slug>/`; inside the shared repo the path is
`projects/<logical/path>/authors/<author-slug>/<session_id>.json` (see the
project layout section below), or `authors/<author-slug>/<session_id>.json`
when no project identity resolves. No mutable pointers are ever written to the
sidecar — because every author appends to a disjoint path and nothing is ever
rewritten, merges are conflict-free by construction. Your local "latest
checkpoint" pointers stay private on your machine and never sync.

`DAIMON_TEAM=1` gates **writes only**. With it unset, your checkpoints are not
mirrored into the sidecar — but reads of the team directory are always on, so you
can still see teammates' memory even before you start contributing your own.

## Project layout: the architect-authored squad tree

Checkpoints in the sidecar are grouped by **logical project**, so several repos
can share one memory pool and the same repo maps to the same pool on every
teammate's machine. The hierarchy is organizational, not forge-derived: a team
architect authors `daimon-team.toml` at the sidecar root — the squad tree with
repos mapped into it — and commits it like any other file. **Daimon only reads
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
— every path segment is munged filesystem-safe, so a config key can never
escape the sidecar.

A session's logical project is resolved in this order:

1. **`DAIMON_TEAM_PROJECT`** — a relative path like `core/api-gateway`, set
   per machine. Explicit local intent; beats the config file.
2. **The `daimon-team.toml` mapping** — the session repo's `origin` URL is
   normalized and matched against every project's `repos`.
3. **Origin-derived fallback** — the origin URL's path without the host
   (`git@github.com:org/repo.git` → `projects/org/repo/…`). Unmapped repos
   still get portable identity, so the config file is optional and
   incremental — add mappings as the squad tree takes shape.
4. **No origin at all** (no git, no remote) — the checkpoint files directly
   under `authors/<author-slug>/`, exactly as before this layout existed.

The config file is optional and can be added or changed at any time — unmapped
repos sync under their origin-derived path from day one. Remapping a repo never
orphans its earlier history: reads cover the previous location too, so
checkpoints already sitting under the old path stay visible after the move.

A broken or unparseable `daimon-team.toml` never blocks a write: the mapping
is treated as absent (resolution falls through to the origin-derived
fallback) and the parse error is surfaced as a warning in `daimon team
status`. Membership (below) is the one exception — it fails **closed**, so a
paste error can never open the remote to the whole machine.

**Legacy note:** sidecars written before this layout keep their flat
`authors/<author-slug>/` files. That era stays readable forever — reads fan in
across both layouts and there is no migration; new checkpoints simply start
landing under `projects/` once a project identity resolves.

## Which projects sync: the scope allowlist (default-closed)

`DAIMON_TEAM=1` is machine-global, but a synced remote only accepts
checkpoints from projects it has **granted membership**. Everything else stays
in the machine-local mirror (`<team_dir>/local/`) — withheld from the remote,
never lost. Without this gate, one enabled remote would receive every project
on the machine, including personal ones.

A project is in scope for a remote when any of these holds:

1. Its origin URL is listed under the sidecar's top-level `[scope]` table:

   ```toml
   [scope]
   repos = ["git@github.com:org/finance-svc.git"]
   ```

2. Its origin URL is mapped under any `[projects.*]` table — a repo the
   architect placed in the squad tree is a member, so existing mapped
   sidecars keep syncing with no new configuration.
3. `DAIMON_TEAM_PROJECT` is set on the machine — explicit local intent.

`daimon team init` seeds a fresh (empty) remote with a `daimon-team.toml`
scoping the team to the project you ran it from, so new setups need no extra
step. Joining an established remote never writes config — the architect owns
the file after birth.

**Migrating an existing sidecar** (created before scoping existed): add the
`[scope]` block above — one line per repo that should sync — commit, and
push. Until then `daimon team status` shows `scope: none — this remote
receives no checkpoints`. If foreign project trees already accumulated under
`projects/`, remove them with plain `git rm -r projects/<path>` + push
(history rewrite optional; the files remain in git history without it).

## Reading teammates

- **`daimon brief --team`** — your normal briefing, plus a Teammates section: one
  attributed block per teammate (excluding yourself), newest first. With no team
  data the section simply doesn't appear.
- **`daimon recall <query>`** — full-text (SQLite FTS5) search over your local
  history *and* the team's.
- **`daimon team status`** — per-remote freshness, your own unpushed checkpoint
  count, the authors seen in the sidecar, and the scope allowlist (which
  repos may sync into each remote).

Teammates' checkpoints are shown within a read-time age window controlled by
`DAIMON_TEAM_RETENTION_DAYS` (default 365; `0` means keep everything). Aging out
is a read filter only — no file is ever physically deleted from the append-only
shared branch.

## Environment reference

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_TEAM` | unset (off) | Set to `1` to mirror your checkpoints into the team dir. Gates writes only; reads are always on. |
| `DAIMON_AUTHOR` | `git config user.name` → OS username → `unknown` | The author name your checkpoints are filed under. |
| `DAIMON_TEAM_DIR` | `~/.daimon/team` | Root of the local team mirror (one subdirectory per sidecar clone). |
| `DAIMON_TEAM_PROJECT` | unset | Explicit logical project path (e.g. `core/api-gateway`) for this machine's sessions. Beats the `daimon-team.toml` mapping and the origin-derived fallback. |
| `DAIMON_TEAM_RETENTION_DAYS` | `365` | Read-time age window for teammates' checkpoints; `0` = keep all. |

See [docs/configuration.md](../getting-started/configuration.md) for the full environment
reference.

## When things go wrong

Sync is fail-open by design: any degraded outcome leaves you on the last synced
state and never breaks the session.

| Situation | What happens |
|---|---|
| Offline | Sync defers; you keep the last synced team state. Your own new checkpoints commit locally and queue for the next push. |
| Missing git credentials | Git runs non-interactively (`GIT_TERMINAL_PROMPT=0`) — it fails fast instead of hanging on a credential prompt, and sync degrades to offline. |
| Concurrent pushes | A rejected push is benign (a teammate won the race). Daimon integrates their change and retries, bounded to 3 attempts, then warns if still rejected. |
| Rewritten shared history | Loud warning; daimon leaves your local copy untouched and refuses to auto-repair. Daimon never force-pushes — resolve it manually with git and your team. |
| Git not installed | Sync is skipped and returns success (rc 0) — team memory is simply inactive. |

## Status

Team memory is early. It is designed for and validated at 1–2 person scale, where
the git sidecar's conflict-free append model and the private-repo boundary hold
up cleanly. It is **not** a defended multi-tenant boundary: the security model is
"everyone in the private repo trusts everyone else in the private repo." Size the
repo's membership accordingly.
