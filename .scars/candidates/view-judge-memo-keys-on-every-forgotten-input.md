---
id: 0
type: landmine
title: view.judge is memoized on more than the bucket's own ledgers, because the forgotten set it judges by is machine-wide
severity: high
confidence: 0.8
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "_judge_memo"
evidence:
  - note: "#1132 PR 9a: tests/test_view_judge.py::test_a_forget_in_another_bucket_drops_the_memo"
expires:
  condition: "the forgotten set is read per bucket only, or the memo is removed"
  review_after: 2027-04-07
status: candidate
---

`view.classify` withholds a value forgotten in ANY local project (and any value
a teammate published a tombstone for). A judge memoized on the stat of its own
bucket's `trust.jsonl` and `events.jsonl` alone would keep serving the old
verdict after a forget in a DIFFERENT bucket, and a long-lived host (the MCP
server, the viewer) would keep indexing and returning the value.

The key therefore also carries `store.forgotten_stamp()`: each local bucket's
`events.jsonl` and each foreign tombstone ledger. A build and a query take it
once per pass and hand it to every `judge(slug, stamp=...)` call. An
UNREADABLE or transient ledger is never memoized, so a repaired ledger shows
on the next call. If you add another input to the forgotten set, add it to
`forgotten_stamp` in the same change.
