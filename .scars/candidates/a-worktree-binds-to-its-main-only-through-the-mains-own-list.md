---
id: 0
type: landmine
title: A worktree may use its main's bucket only when the main's OWN `git worktree list -z` names its exact real path, never because its `.git` file says so
severity: high
confidence: 0.85
created: 2026-11-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/hostgit.py
  - pattern: "linked_main"
evidence:
  - note: "Design rev 5 worktree section and rev 3 refutation N5; tests/test_identity_worktrees.py plants a spoofed gitfile, a lock reason with a forged record, a submodule and a bare main"
expires:
  condition: "worktree membership is declared by the host or stored in the bucket instead of asked of git"
  review_after: 2027-05-23
status: candidate
---

A linked worktree's `.git` is a plain file anyone can write, so it only picks
WHICH repository git is asked about. `hostgit.linked_main` accepts the answer
when the first entry of that repository's worktree list is a non-bare main
directory, the path's realpath equals one of the LINKED entries, and the main
is not the path itself (a submodule lists itself first). A directory that
points at a victim's admin dir is not in the victim's list, so it keeps a
bucket of its own.

`-z` is required, with no newline fallback: without it a newline in a path or
a lock reason can forge an entry. Only a field that starts with `worktree `
opens a record. Git older than 2.36 fails the call and the worktree keeps its
own bucket, which is wrong in the safe direction.

Two guards keep this off the hot path and out of the narrow scopes: one lstat
of `.git` decides, and git is forked only for a regular file (an ordinary repo
never forks); SEALED and tenant scope never ask. The answer is memoized and
must be cleared with `config.resolve_project_root.cache_clear`, which now
clears both memos. Do not call `git worktree list` from anywhere else.

Known residual: a stale entry left by moving a worktree still names its old
path, so whoever puts a directory there inherits the binding until
`git worktree prune`.
