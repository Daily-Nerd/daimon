---
id: 0
type: landmine
title: In a worktree the guard blocks git outside it but not file edits, so a docs change lands in the shared checkout and the gates still pass
severity: medium
confidence: 0.9
created: 2026-09-07
authors: ["claude-code"]
anchors:
  - path: docs/
  - path: website/docs/
  - pattern: "cd /Users/[a-z]+/Documents/Daily-Nerd/daimon &&"
evidence:
  - note: "#963, fix/963-bucket-migration: five doc files were edited from the shared checkout path while the branch lived in a worktree. git status in the worktree showed no docs change, and the reader-vocabulary and website gates ran green against the worktree's unedited copies."
expires:
  condition: "the worktree isolation guard refuses non-git writes to the shared checkout too"
  review_after: 2027-03-01
status: candidate
---

An agent working in `.claude/worktrees/<id>` is stopped by the isolation guard
the moment it runs `git` against the shared checkout. It is NOT stopped from
READING or WRITING files there. A command shaped
`cd /Users/.../Documents/Daily-Nerd/daimon && python3 - <<PY ... PY` that edits
`docs/configuration.md` therefore succeeds, and the edit lands on the shared
working tree instead of the branch.

Two things then hide it. `git status` in the worktree is clean for those paths,
so the change looks like it was never made. Worse, the doc gates keep passing:
`test_reader_facing_vocabulary.py` and the website tests resolve their paths
from the worktree root and read the PRISTINE copies, so a green gate proves
nothing about the prose that was actually written. On #963 the vocabulary gate
was run and reported clean before the files were ever moved into the branch.

What to do instead: build every path from the worktree root the environment
prints, never from the repo name, and treat a docs edit as unverified until
`git status` in the worktree lists the file. Recovering afterwards means
copying the worktree's pristine copy to the scratchpad, copying the edited file
from the shared checkout into the worktree, then restoring the shared checkout
from the pristine copy, since git cannot be used to reset a path over there.
