---
id: 129
type: fence
title: "A snapshot or masker that fails in the decide queue fails the call as MaskFailed; only an unreadable ledger drops a lane"
severity: medium
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/pending.py
evidence:
  - commit: e5aea9e
  - note: tests/test_pending_masking.py::test_a_judge_that_fails_fails_the_call_not_a_lane
expires:
  condition: "the lanes stop being fail-open per source"
  review_after: 2027-04-09
status: active
---

Each queue lane is wrapped in `except Exception: pass`, so an unreadable ledger degrades its lane. A snapshot or
masker failure must not take that path: the lane would vanish and `decide` would print "nothing waiting", a
false claim. `_queue` and `_masked_lane` turn both into `MaskFailed`, which the lane loops and
`foreign_queues_typed` re-raise, so `decide` answers with one error line and exit 2 (guarded). The briefing's
count passes `masked=False` and takes neither. Do not widen the lane `except` to swallow `MaskFailed`.
