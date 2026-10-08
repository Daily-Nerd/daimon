---
id: 120
type: landmine
title: view.judge must not memoize a judge built while some events ledger was unreadable, because a cleared read error changes no stat
severity: high
confidence: 0.85
created: 2026-10-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "_judge_memo\[memo_slot\]"
evidence:
  - note: #1132 PR 10a: tests/test_forgotten_incomplete.py::test_recall_rebuilds_once_the_incomplete_bucket_is_proven_again fails if the judge is kept
expires:
  condition: "the judge memo key includes the health of every events ledger, not only its stat"
  review_after: 2027-04-08
status: active
---

`view.judge` memoizes a bucket's judge on the stat (inode, mtime, ctime, size)
of its trust and events ledgers and of everything that feeds the machine-wide
forgotten set. A chmod moves ctime and nothing else, so with ctime in the key a
permission change that breaks or fixes a ledger is seen without clearing a
cache. A read error that simply clears (an EIO that stops, a transient lock)
changes no stat at all.

A judge built while ANOTHER bucket's events ledger could not be read holds a
forget set that is missing that bucket's tombstones. If it were memoized, the
stale judge would keep serving after the error cleared and the forgotten value
would stay visible with no note. `judge` therefore keeps an entry only when
`store.forgotten_incomplete()` is empty (a caller that judges many buckets
passes that set in once per pass). Do not widen the memo "for speed" without
putting the health of every events ledger into its key.
