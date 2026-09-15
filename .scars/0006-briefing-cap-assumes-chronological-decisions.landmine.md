---
id: 6
type: landmine
title: Briefing decision-cap keeps the TAIL of recent_decisions — it silently breaks if the serializer stops emitting decisions oldest→newest
severity: medium
confidence: 0.9
created: 2026-06-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - path: plugin/daimon_briefing/serializer.py
  - path: plugin/daimon_briefing/carry.py
evidence:
  - issue: 77
  - issue: 1034
expires:
  condition: "briefing.build stops using positional tail-slicing for the decision cap (e.g. switches to explicit per-item timestamps)"
  review_after: 2027-06-30
status: active
---

`briefing.build()` caps decisions to the most-recent N by slicing the TAIL:
`decisions[-n:]`. "Most-recent" is therefore positional — it assumes
`recent_decisions` is ordered oldest→newest.

That ordering is a CONTRACT, not an accident: the serializer prompt instructs
`CHRONOLOGY: for recent_decisions … order items in the sequence they were made`.
The cap silently depends on it. If a future serializer change (reordering, a
merge pass that sorts by topic, importance-ranking, etc.) emits decisions in any
other order, the briefing will keep the wrong N and drop the genuinely-recent
ones — with NO error, NO test failure (the cap tests use synthetic chronological
fixtures), and NO visible symptom beyond a subtly wrong briefing.

If you change how `recent_decisions` is ordered in the serializer, either keep
oldest→newest, or change `briefing.build()`'s selection to be order-independent
(e.g. per-item timestamps) at the same time. Do not assume the tail is the
newest just because it is today.

2026-09-15 (#1034): this fired, and the serializer was never the module that
broke it. `carry.merge` ends with `native.extend(carried[:cap])`, appending the
previous checkpoint's items — older than every native one by construction — at
the TAIL of the same list, so the tail-slice kept the carried block and dropped
the decisions the session had just made (measured: 8 carried and 2 native
rendered, 11 native dropped, the cap biting on 80 of 83 checkpoints in one
chain). `carry.py` is anchored here now: ANY module that appends to, sorts, or
reorders `recent_decisions` owns this contract, not just the one that first
writes the list. The fix was render-side (`briefing._select_decisions` picks the
native tail first, carried by weight after), so the positional assumption is
gone from the cap but the checkpoint's own ordering is still a contract.
