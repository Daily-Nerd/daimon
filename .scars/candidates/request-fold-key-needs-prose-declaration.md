---
id: 0
type: landmine
title: A new request fold key that carries a person's text must be declared in folded_prose, MASK_POLICY, the pin test and the sentinel world, or the read-sentinel suite fails
severity: medium
confidence: 0.8
created: 2026-11-13
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/surfaces.py
  - path: plugin/daimon_briefing/view.py
evidence:
  - note: "issue 1333, superseded_claims[].evidence added to the request fold"
expires:
  condition: "folded prose is derived from the fold itself instead of declared by hand in four places"
  review_after: 2027-05-13
status: candidate
---

The request fold copies prose into keys that sit outside the row paths (`done_evidence`,
`replies[]`, and since #1333 `superseded_claims[].evidence`). A printer shows the folded
record, so each such path must be declared in four places that nothing links:
`Surface.folded_prose` in surfaces.py, `MASK_POLICY` in view.py, the pinned tuple in
tests/test_surface_registry.py, and a real writer call in tests/_sentinel_world.py
(`_write_folded_prose`), because test_the_prose_columns_are_planted_in_the_world fails
on any declared path no sentinel reaches. Missing the first two leaks the text past the
mask; missing the last two fails CI far from the change. The effect table
(`row_effects`) holds reason codes only and needs none of this: never put prose in it.
