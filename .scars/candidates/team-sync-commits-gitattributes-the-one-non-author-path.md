---
id: 0
type: fence
title: team init commits .gitattributes on an established remote on purpose; it is the one non-author path daimon writes to the shared branch
severity: medium
confidence: 0.8
created: 2026-10-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/teamsync.py
  - pattern: "_ensure_union_rules"
evidence:
  - commit: 186de19
  - note: tests/test_teamsync.py::test_init_appends_only_the_missing_rules_to_an_existing_file
  - note: tests/test_teamsync.py::test_sync_of_two_machines_of_one_author_keeps_both_rows
expires:
  condition: "the merge=union rule moves to .git/info/attributes (per clone, never committed) or git merges append-only ledgers by union without a rule"
  review_after: 2027-04-23
status: candidate
---

The module docstring says only immutable per-author files are synced and
`_commit_own` is pathspec-scoped to the author's own directories. `init`
breaks that pattern once, by design: on an established remote whose
`.gitattributes` lacks the `merge=union` rules for the published ledgers it
appends the missing lines and commits the file, and the next sync pushes it.

It looks like the kind of daimon-written shared state the invariant forbids,
and a future editor may be tempted to drop the commit and keep only the
working-tree write. Do not. The rule has to reach every clone, and `team init`
refuses an existing clone, so an old clone can only receive it through a
fetch. Without the commit a machine upgraded after the remote was born keeps
tearing the ledgers on every two-machine append (test_teamsync.py,
test_sync_of_two_machines_of_one_author_keeps_both_rows is the shape).

The write is safe for the same reason the ledgers are: its content is fixed
(`LEDGER_UNION_GLOBS`), so two machines committing it concurrently produce
identical adds or identical appended hunks, which git merges clean. Keep the
commit pathspec-scoped and never let `_commit_own` sweep it: that function
must stay own-author only, or an unrelated pre-staged file in the clone would
ride the team branch (#144).
