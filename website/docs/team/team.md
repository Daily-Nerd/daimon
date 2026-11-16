---
description: "Share agent memory across a team through a git sidecar repo. Checkpoints stay immutable and attributed, and the shared repo is the privacy boundary."
---

# Team memory

Team memory mirrors each of your session checkpoints, immutable, one file per
author, through a shared git sidecar repo, so a whole team's memory converges
without merging anyone's notes into anyone else's. Once it's enabled, teammates'
active topics and decisions show up attributed (never blended into your own) in
`daimon brief --team`, and their history is searchable alongside yours in
`daimon recall`.

## The shared repo is the privacy boundary, read this first

**The sidecar's access control IS the membership and privacy boundary. Whoever
can read that git repo can read everything every member's sessions produced, so
the remote MUST be a private repository.** There is no second gate. When team
memory is on, whatever a session discussed is mirrored to the shared repo
*verbatim*, after one pass of shape-based secret redaction.

That redaction is deliberately narrow. It catches secrets with a concrete,
recognizable shape, assignment-style `api_key=…` / `token:…` pairs, PEM key and
certificate blocks, `Bearer` headers, `credential://user:pass@host` URLs, and
fixed-prefix vendor tokens (AWS `AKIA…`, Stripe `sk_live_…`, GitHub `ghp_…`,
GitLab, Slack, Google, OpenAI keys, JWTs). Each match is replaced with a visible
`[redacted:<kind>]` marker. It does **not** understand meaning: free-text
confidential prose, a customer name, an unreleased plan, an internal URL written
as ordinary words, is not a secret shape and syncs through untouched. Treat the
shared repo as if every member can read every word, because they can.

Author identity is declared, not authenticated. Any member of the repo can write
under any author name; daimon cross-checks the name stamped in each arriving file
against the git author who committed it and surfaces a mismatch as a warning, but
it never blocks the write. The repo's access control is what keeps outsiders out
,  the author label only tells you who a file *claims* to be from.

## What happens, and when

At the start of every session, daimon fires `daimon team sync` detached in the
background. It never blocks your briefing and never fails your session, if
anything goes wrong the session proceeds exactly as if team memory were off. The
spawn is gated on a team remote actually existing, so machines that never ran
`daimon team init` pay only a directory scan.

A sync pass does three things, in order:

- **Commits and pushes your own author directory, plus the merge rules.** It
  stages and commits new files under `authors/<your-author-slug>/`: never
  another author's files, never anything you happened to leave staged in the
  sidecar. The one other path it commits is the clone-root `.gitattributes`,
  when the `merge=union` rules for the published ledgers (and for the
  attributes file itself) are missing from it; the content of those lines is
  fixed, so two clones committing them merge clean.
- **Fetches teammates' updates**, but only when the remote actually changed. A
  refs-only `ls-remote` probe compares hashes first; objects transfer only on a
  mismatch, so a no-op sync costs one lightweight network round-trip.
- Leaves everything else alone.

Only immutable per-author checkpoint files ever sync. On disk they live under
`~/.daimon/team/<remote-slug>/`; inside the shared repo the path is
`projects/<logical/path>/authors/<author-slug>/<session_id>.json` (see the
project layout section below), or `authors/<author-slug>/<session_id>.json`
when no project identity resolves. No mutable pointers are ever written to the
sidecar, because every author appends to a disjoint path and nothing is ever
rewritten, merges are conflict-free by construction. Your local "latest
checkpoint" pointers stay private on your machine and never sync.

`DAIMON_TEAM=1` gates **writes only**. With it unset, your checkpoints are not
mirrored into the sidecar, but reads of the team directory are always on, so you
can still see teammates' memory even before you start contributing your own.

## Set up and configure

- [Set up team memory](./setup.md): the two-person setup, step by step.
- [Team configuration](./configuration.md): `daimon-team.toml`, the scope allowlist and the environment variables.
- [Troubleshooting](../guides/troubleshooting.md): what happens when sync cannot reach the remote.

## Reading teammates

- **`daimon brief --team`**, your normal briefing, plus a Teammates section: one
  attributed block per teammate (excluding yourself), newest first. With no team
  data the section simply doesn't appear.
- **`daimon recall <query>`**, full-text (SQLite FTS5) search over your local
  history *and* the team's.
- **`daimon team status`**, per-remote freshness, your own unpushed checkpoint
  count, the authors seen in the sidecar, the scope allowlist (which repos may
  sync into each remote), a warning when the clone's `.gitattributes` lacks
  the `merge=union` rule for the published ledgers, and one line for the
  current project when its active quarantines are not yet published (see
  "Quarantines travel too" below).

Teammates' checkpoints are shown within a read-time age window controlled by
`DAIMON_TEAM_RETENTION_DAYS` (default 365; `0` means keep everything). Aging out
is a read filter only, no file is ever physically deleted from the append-only
shared branch.

### The inbound gate

Every policy daimon enforces on your own checkpoints on the way out is
re-asserted on a teammate's checkpoint on the way in, at read/index time
(in-memory, sidecar files and git history are never rewritten):

- **Scope**: content from a remote that doesn't pass the scope check above
  never reaches the briefing or the recall index.
- **Redaction**: your local secret patterns re-scrub incoming text before it
  is shown or indexed. A teammate on an older daimon with fewer patterns
  can't seed a durable cleartext copy on your machine.
- **Forget**: values you tombstoned with `daimon forget` are dropped from
  incoming checkpoints too: a teammate's copy can't re-surface something you
  deleted locally.
- **Trust**: a teammate's `verbatim` item is stored and scored as
  `inferred`, because its receipt can only be verified on the machine that
  captured it. In the Teammates section it renders with a visible label:
  `[teammate claims verbatim — unverifiable here]`, which states both facts:
  the claim, and that it can't be checked locally. Your own items are
  unaffected.

### Quarantines travel too

A quarantine is a person's decision that a value is unsafe to act on
(`daimon trust propose`, confirmed or released by a human). When you turn one
on, or lift one, and a team is enabled, daimon appends a hash-only row to your
own `quarantines.jsonl` beside your tombstones in each sidecar the project
routes to: the kind, a hash of the value, `active` or `released`, an ordering
stamp and ids. Never the text, the reason or the evidence. An agent's
candidate and a dismissal publish nothing. A release also reaches every sidecar
of yours that still holds the claim, whether or not the project routes there
now.

- **Scope.** A teammate's quarantine withholds that value, of that kind, on
  your whole machine, in every project, the way a forget does. You see
  `[withheld: quarantine]` with no id, on every channel, a terminal included.
  `daimon forget`, `daimon resolve` and `daimon reverify` on such an exact id
  are refused ("a teammate quarantined this; they release it"). Only they
  release it.
- **Yours stay yours.** Your own quarantines keep working per project from your
  local ledger. The same person on a second machine has to propose there too.
- **See them.** `daimon trust list` ends with a count of the quarantines
  teammates have in force. `daimon trust list --team [--json]` shows kind and
  count anywhere, and the author directory and time at a terminal.
- **Re-send.** `daimon trust republish` (a person at a terminal, never
  `--by agent`) re-sends the standing quarantines of the project and the latest
  release of each released one, unchanged. Use it after a failed publish
  (`trust` exits 4 and says which sidecar), after enabling the team, after being
  granted a sidecar, or after `daimon ledger repair trust`. Several authors
  fold by union: one teammate's release lifts only their own claim.
- **See what is still local.** `daimon team status` ends with one line for the
  project you run it from when its ledger holds an active quarantine whose
  latest row is missing from a clone the project routes to:
  `<project>: 2 active quarantine(s) not yet published; run daimon trust
  republish`. Presence is checked by the row's id, the way the publisher
  checks it, and the command writes nothing. A released quarantine never
  counts. When a clone's ledger cannot be read the line says so instead of
  reporting zero. With `--json` each routed remote's row carries the same
  count under `unpublished_quarantines`.
- **Authority.** Sidecar write access is the trust boundary, as for checkpoints
  and tombstones. Published rows are declared claims, not authenticated ones. A
  member with write access can over-suppress by publishing an `active` row, and
  can lift a teammate's quarantine with a `released` row or by deleting lines
  (deleting a tombstone already re-exposes a forgotten value). The sync warns
  when a commit by someone other than the directory's owner changed a
  published ledger. That is hygiene, not proof, and it does not detect a forged
  row. Published rows are version 1; a reader that meets a row version it does
  not know skips that author until upgraded.
- **A forget is not retracted.** A published tombstone row has no retraction
  because tombstone rows carry no version and no fail-closed hook; that gate
  exists for quarantine rows only. `daimon unforget` lifts a forget on your own
  machine and leaves the row you published: the row your teammates already
  folded stays, and they keep withholding the value until they remove it
  themselves.
- **A damaged ledger skips the author.** A garbage line or a torn last line in
  either published ledger of a teammate means their newest claim cannot be
  proven, so their checkpoints are not admitted until the file is clean, and
  you get the `author-skipped` note. The good rows before it still count.
  Anyone with write access to the sidecar can do this to any author for the
  whole team with one damaged line in that author's file; the cure is the owner
  fixing the file in git.
- **What it does not do.** The mirror still carries a checkpoint's text, and
  git history keeps it. A quarantine withholds on read and never rewrites;
  `daimon forget` is the deletion tool.

## Status

Team memory is early. It is designed for and validated at 1–2 person scale, where
the git sidecar's conflict-free append model and the private-repo boundary hold
up cleanly. It is **not** a defended multi-tenant boundary: the security model is
"everyone in the private repo trusts everyone else in the private repo." Size the
repo's membership accordingly.
