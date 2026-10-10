---
id: 0
type: fence
title: trust republish re-sends the original local rows verbatim, because presence and ordering key on order and event_id
severity: medium
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/trust.py
  - pattern: "def republish"
evidence:
  - commit: acc8142
  - note: tests/test_quarantine_publish.py::test_republish_resends_the_latest_active_row_verbatim
expires:
  condition: "the published row is ordered by something other than the local ledger's order and event_id"
  review_after: 2027-04-09
status: candidate
---

A published quarantine row copies `order`, `event_id` and `ts` from the local
ledger row that caused it, so the reader's per-id fold orders it exactly as the
local fold would, and a retry is a no-op because presence is checked by
`event_id`. `daimon trust republish` therefore sends the latest local
activating or releasing row unchanged.

Stamping a fresh row at republish time looks harmless and is wrong twice: a
re-sent activation would carry a newer `order` than a release the owner made
meanwhile and so undo it in the fold, and every run would append a duplicate
because no `event_id` repeats. Keep the original identity, and keep the
release fan-out limited to sidecars whose file still folds that id active.
