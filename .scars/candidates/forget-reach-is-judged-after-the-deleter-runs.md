---
id: 0
type: fence
title: Forget decides "reached" by reading the ledger again after the deleter, never from the deleter's own return
severity: medium
confidence: 0.85
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - pattern: "judge_reach"
evidence:
  - note: "#1132 PR 10b: tests/test_forget_reached.py::test_a_failed_rewrite_is_not_reached_even_on_a_clean_ledger"
expires:
  condition: "every deleter reports success or failure itself, distinct from 'nothing matched'"
  review_after: 2027-04-08
status: candidate
---

Every forget deleter returns `[]` for "nothing matched", "ledger absent" and
"rewrite failed" alike, and the suites assert list equality on those returns, so
the return carries no failure signal. `jsonl.reaching` therefore wraps each
deleter and judges the ledger AFTER it ran: unproven (TRANSIENT or any
UNREADABLE) is unreached, a plaintext ledger with a torn line is unreached
(`rewrite` writes a torn line back verbatim and it may hold the value), and so is
any row that still holds the value, which is what a failed swap on a healthy
ledger looks like. Do not replace this with a flag inside the deleters without
keeping the post-read: it is the only check that sees the file as it is now.
