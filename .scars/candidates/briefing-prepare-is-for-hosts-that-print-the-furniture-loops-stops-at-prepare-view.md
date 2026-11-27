---
id: 0
type: landmine
title: Anything added to briefing.prepare taxes daimon loops; the furniture belongs behind prepare, loops stops at prepare_view
severity: medium
confidence: 0.85
created: 2026-11-26
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - path: plugin/daimon_briefing/cli/lifecycle.py
evidence:
  - commit: df734bd
  - note: "measured on a synthetic store of 120 buckets: daimon loops 35 ms to 110 ms and 744 to 2464 ledger reads when prepare built the request furniture; 15 ms and 133 reads once loops called prepare_view"
expires:
  condition: "daimon loops prints the request panels, or prepare_view and prepare merge again"
  review_after: 2027-05-26
status: candidate
---

`prepare_view` is the shared reading of a checkpoint (the view, the stamps,
the notes, the baton). `prepare` is `prepare_view` plus the furniture every
plain briefing host prints: the rulings, the decision count, the four request
panels, the version note. The furniture reads the requests ledgers of this
project and of every project that exchanges requests with it, so it is the
expensive part.

`daimon loops` lists open items and prints none of that. When `prepare`
built the furniture for it, `loops` read about 3.3 times more ledgers on a
store with many buckets and ran about 3 times slower. Nothing failed, so the
cost was invisible until measured. `test_daimon_loops_builds_none_of_the_
briefing_furniture` pins it: `_furniture` is patched to raise and `loops` must
still list.

When a new reader of a checkpoint is added, call `prepare_view` unless it
prints the furniture. Add new briefing-only reads to `prepare`, never to
`prepare_view`.
