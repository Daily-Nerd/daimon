---
id: 140
type: fence
title: why reports the evidence axes of a withheld item from Withheld.receipt, a textless projection the view takes while it filters the copy, never from a raw read in the inspector
severity: medium
confidence: 0.75
created: 2026-10-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/inspector.py
  - pattern: "def _withheld_result\("
  - pattern: "def _projection\("
evidence:
  - note: #1132 PR 11a: tests/test_why_contract.py::test_a_withheld_items_bound_receipt_still_reports_its_axes and ::test_the_view_projection_carries_no_text
expires:
  condition: "the pointer-window filter keeps the removed item reachable to the view, or a person rules the axes may go"
  review_after: 2027-04-09
status: active
---

A `Withheld` carries no item: the pointer-window filter deletes it from the
copy it hands out. The capture, provenance, locator, bytes and verifier axes
rest on the item's `quote_provenance` receipt, so `_filter` takes
`view._projection(item)` before the delete: the receipt when it is
structurally valid (an invalid one is dropped whole) and the two origin
stamps. It rides on `Withheld.receipt`, outside equality, and has no text.

`_withheld_result` feeds it to the same `_Evidence` the visible path uses and
publishes the axes only: the receipt, the source, the quote and the transcript
window stay out of the payload. An id that is only a tombstone, or an index
row, has no projection and reports unknown. Do not read the pointer from the
inspector to fill that gap, and do not add a text field to the projection.
