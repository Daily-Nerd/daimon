---
id: 0
type: landmine
title: view.judge must not memoize a judge built while some events ledger was unreadable, because its stat key cannot see the ledger become readable again
severity: high
confidence: 0.85
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "_judge_memo\[memo_slot\]"
evidence:
  - note: "#1132 PR 10a: tests/test_forgotten_incomplete.py::test_recall_rebuilds_once_the_incomplete_bucket_is_proven_again fails if the judge is kept"
expires:
  condition: "the judge memo key includes the health of every events ledger, not only its stat"
  review_after: 2027-04-08
status: candidate
---

`view.judge` memoizes a bucket's judge on the stat (inode, mtime, size) of its
trust and events ledgers and of everything that feeds the machine-wide
forgotten set. A judge built while ANOTHER bucket's events ledger could not be
read holds a forget set that is missing that bucket's tombstones. When the
ledger reads again because someone fixed its permissions, or the disk error
cleared, no stat field changed (a chmod touches ctime only), so the stale judge
kept serving and the forgotten value stayed visible with no note.

`judge` therefore keeps an entry only when `store.forgotten_incomplete()` is
empty. Do not widen the memo "for speed" without putting the health of every
events ledger into its key.
