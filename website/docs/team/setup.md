---
sidebar_position: 2
description: "Set up team memory between two people: create the private sidecar repo, point each machine at it, and check that checkpoints sync."
---

# Set up team memory

This is the two-person setup, step by step. What team memory shares, and why the shared repo is the privacy boundary, is in [team memory](./team.md). The `daimon-team.toml` format and the environment variables are in [team configuration](./configuration.md).

Do this once per shared repo, then once on each machine.

**1. Create an empty private git repo** on your host of choice (GitHub, GitLab,
self-hosted, anything git can clone over SSH or HTTPS). Leave it empty; the
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

An empty remote is fine, the first machine to init seeds a root commit and
pushes it. Running init a second time against a directory that already exists is
an error, not a re-clone.

`team init` also writes a `.gitattributes` at the root of the clone with a
`merge=union` rule for the published ledgers (`authors/*/tombstones.jsonl`,
`authors/*/quarantines.jsonl` and their `projects/**/` forms, plus one for the
attributes file itself, so two clones' copies of the rules merge clean). Both ledgers are
append-only and each author writes only their own file, so the one conflict two
machines of one author can produce is both appending a line after the same
base; the rule keeps both lines instead of leaving conflict markers that make
the reader skip the author. On an existing file only the missing lines are
appended. `daimon team sync` writes and commits the rule before it merges, so
a clone made before the rule existed receives it on its next sync and the
team's clones converge; do not add the lines by hand, an untracked copy blocks
the merge that brings a teammate's committed copy in. `daimon team status`
names a clone whose file lacks the rule.

**4. Verify** on each machine:

```sh
daimon team status
```

You should see the remote listed with its freshness and the authors seen so far.
