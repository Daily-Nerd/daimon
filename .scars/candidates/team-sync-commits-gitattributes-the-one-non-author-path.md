---
id: 0
type: fence
title: team sync commits .gitattributes on purpose; it is the one non-author path daimon writes to the shared branch, and an untracked copy is the trap
severity: medium
confidence: 0.8
created: 2026-10-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/teamsync.py
  - pattern: "_commit_union_rules"
evidence:
  - commit: aa45bd7
  - commit: d2b0ebd
  - note: tests/test_teamsync.py::test_sync_adopts_a_hand_written_untracked_attributes_file
  - note: tests/test_teamsync.py::test_sync_gives_an_old_clone_the_rules_and_appends_then_merge_by_union
expires:
  condition: "the merge=union rule moves to .git/info/attributes (per clone, never committed) or git merges append-only ledgers by union without a rule"
  review_after: 2027-04-23
status: candidate
---

The module docstring says only immutable per-author files are synced and
`_commit_own` is pathspec-scoped to the author's own directories. `sync_remote`
breaks that pattern once, by design, in `_commit_union_rules`: before any
merge of the pass it appends the missing `merge=union` lines to
`.gitattributes`, `git add`s the file and commits it when the index changed.
`init` only writes the file (and seeds it on a fresh remote).

It looks like the daimon-written shared state the invariant forbids, and a
future editor may be tempted to drop the commit and keep a working-tree
write, or to tell the owner to add the lines by hand. Do not. Once any clone
has committed the file, a plain merge into a clone that holds an UNTRACKED
copy refuses with "untracked working tree files would be overwritten",
byte-identical content included (reviewer repro, pinned by the first test
above). The only cure is for the file to be tracked in every clone before
its merge, and the only actor present in every clone before every merge is
the sync itself. `team init` cannot do it for old clones: it refuses an
existing directory.

The write is safe for the same reason the ledgers are: the content is fixed
(`LEDGER_UNION_GLOBS`), so two clones committing it concurrently produce an
identical add or an identical appended hunk, which git merges clean. A
hand-written copy with DIFFERENT lines still conflicts on the file itself and
surfaces as the existing "merge failed" warning. Keep the commit
pathspec-scoped and never let `_commit_own` sweep it: that function must stay
own-author only, or an unrelated pre-staged file in the clone would ride the
team branch (#144).
