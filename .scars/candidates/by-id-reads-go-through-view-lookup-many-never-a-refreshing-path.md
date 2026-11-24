---
id: 0
type: fence
title: Every by-id read of the recall index goes through view.lookup_many and index_locate (read-only, never refreshed); there is no refreshing by-id path, do not add one
severity: high
confidence: 0.85
created: 2026-11-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/index_locate.py
  - pattern: "def lookup_many\("
  - pattern: "def _loop_texts\("
violation: "recall\.(find|find_many|lookup_item)\("
evidence:
  - commit: c813e73
  - note: "#1407: pending.queue called recall.find once per amendment row; find ran _ensure_fresh, so one queue read could rebuild the machine-wide recall.db per row. Fixed by reading loop text through view.lookup_many and deleting recall.find and recall.lookup_item"
  - note: "Replaces scar 0136 (its last sentence, recall.find stays for pending._loop_text, is false after #1407). Archive 0136 when promoting this one"
  - note: "#1132 PR 11a: tests/test_view_lookup_many.py::test_an_id_nothing_holds_costs_no_session_parse_and_no_rebuild and tests/test_why_cost.py patch recall.rebuild, _rebuild_forced and _ensure_fresh to raise and run a lookup"
expires:
  condition: "the locator is replaced by a bucket-local id table, or recall.db stops being a machine-wide derived index"
  review_after: 2027-05-23
status: candidate
---

An exact-id read (why, lookup, the amendment lane's loop text in
`pending._loop_texts`) goes through `view.lookup_many`: the pointer window,
then `index_locate.locate` with the index opened `mode=ro`, one indexed query
per chunk, never refreshed or written. Masking comes from the view.

The refreshing paths (`query`, `search`, `suggest`) stat every checkpoint,
ledger and team file (`_ensure_fresh`) and rebuild on drift or a withheld row
(`_rebuild_forced`). A by-id read through them turns a display read into a
write of the derived index; #1407 found one queue read rebuilding it once per
row. `recall.find`, `recall.lookup_item` and a short-lived `recall.find_many`
were deleted for this reason. Do not bring back a by-id helper in recall.py;
batch ids into one `view.lookup_many` call instead.
