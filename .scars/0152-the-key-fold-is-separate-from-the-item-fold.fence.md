---
id: 152
type: fence
title: fold_resolutions returns item refs only; key refs are read by standing_tombstones
severity: medium
confidence: 0.8
created: 2026-10-10
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def fold_resolutions"
evidence:
  - commit: ed7fd95
expires:
  condition: "no reader of the item fold counts or prints a ref"
  review_after: 2027-04-10
status: active
---

`store.fold_resolutions` skips refs in the `forgotten-key:` namespace, and
`store.standing_tombstones(rows)` is the one parser that reads both kinds. The
alternative, putting key refs in the item fold and filtering them at each
reader, is wrong in a way no one notices: the viewer ledger subtracts
forgotten refs from `Snapshot.resolved_refs`, so a filter in `view.events`
would stop the subtraction and every forget would add one phantom event to the
total. `stats`, recall marks, the privacy residue audit and `ledger_repair` all
iterate the same map and would each need the same filter.

If a new reader needs key refs, give it `standing_tombstones`. Do not add the
refs back to the item fold; every reader would have to know.
