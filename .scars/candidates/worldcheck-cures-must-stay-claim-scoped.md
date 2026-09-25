---
id: 0
type: fence
title: A confirmation clears only its own claim; any other contradiction still demotes recall
severity: high
confidence: 0.95
created: 2026-09-24
authors: ["codex"]
anchors:
  - pattern: "latest_invalidation_verdicts|latest_world_verdicts|append_world_cure"
evidence:
  - note: "test_receipt_cure_cannot_clear_world_contradiction reproduces the cross-claim cure bug for file, branch, and PR checks; test_world_cure_cannot_clear_receipt_contradiction pins the reverse direction."
expires:
  condition: "Recall stores and ranks each independent claim separately instead of summarizing an item's evidence in scalar columns"
  review_after: 2027-03-24
status: candidate
---

An item can have both a valid receipt and a contradicted file, branch, or PR
claim. Taking the latest verdict across the whole item lets a receipt
confirmation erase unrelated contradiction evidence. Fold world verdicts by
item, probe class, and claim hash (target plus expected assertion), then let
any outstanding contradiction win when summarizing the item for recall.
The cure writer must use that same claim-specific fold. Keep the receipt-only
view for receipt telemetry; it does not describe the item's overall standing.
The tests named above pin both directions and a different claim of the same class.
