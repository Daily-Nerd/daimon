---
id: 0
type: fence
title: "pending._queue takes the bucket snapshot outside the per-lane try on purpose"
severity: medium
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/pending.py
evidence:
  - commit: 3622d7f
  - note: "tests/test_pending_masking.py::test_a_judge_that_fails_fails_the_call_not_a_lane"
expires:
  condition: "the lanes stop being fail-open per source"
  review_after: 2027-04-09
status: candidate
---

Each queue lane is wrapped in `except Exception: pass`, so one unreadable ledger degrades its lane. A masker
that raised inside a lane would therefore empty the lane silently and `decide` would print "nothing waiting",
which is a false claim. `_queue` takes `view.judge(slug).snap` before any lane runs and outside the try, so a
judge that fails fails the call and `decide` answers with one error line and exit 2. A masker that fails after
a good judge still drops its lane (less is shown, never more). The briefing's count passes `masked=False` and
takes neither.
