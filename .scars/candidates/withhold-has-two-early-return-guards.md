---
id: 0
type: landmine
title: briefing.withhold() has TWO early-return no-op guards a new suppression pool must both list, or it silently does nothing
severity: medium
confidence: 0.85
created: 2026-09-24
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - pattern: "not resolved_refs and not candidate_refs and not agent_claim_refs"
evidence:
  - note: "#1109 PR 2 (Daily-Nerd/daimon) — implementing quarantine's read-path
      withholding in briefing.withhold()"
expires:
  condition: "withhold() is refactored to a single dispatch table over named
    outcome pools instead of two hand-listed boolean guards"
  review_after: 2027-03-24
status: candidate
---

`withhold(checkpoint, resolutions, amendments=None, quarantine=None)` has two
separate short-circuit checks that both exist purely as a perf/no-op
optimization: one at the very top (`if not isinstance(checkpoint, dict) or
(not resolutions and not amendments): return checkpoint, [], []`) before any
per-outcome pool is even built, and a second one after `resolved_refs`,
`candidate_refs`, `agent_claim_refs`, and `amend_refs` are computed (`if (not
resolved_refs and not candidate_refs and not agent_claim_refs and not
amend_refs): return checkpoint, [], []`) but before the per-item loop runs.

Adding a SIXTH outcome (here: `quarantine`, a `{(kind, value_key)}` set) means
both guards must be updated to also test the new pool's emptiness, or the
function returns the checkpoint unchanged whenever every OTHER pool happens to
be empty — even though the new pool has real content the per-item loop below
would otherwise have honored. This fails silently: no exception, and any test
that also passes a non-empty `resolutions`/`amendments` alongside the new
argument never exercises the bug, because the second guard's `or` already
keeps going past it. Only a test that calls the function with the new
argument ALONE (empty resolutions and amendments) catches a missed update to
either guard.

A future editor adding a seventh outcome must grep both `return checkpoint,
[], []` lines in this function and extend each one's boolean list — there is
no single place to add to.
