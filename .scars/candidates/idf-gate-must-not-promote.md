---
id: 0
type: fence
title: The #470 idf mass gate is a POST-SELECTION filter on purpose — gating during selection promotes the exempt class and makes recall LOUDER
severity: high
confidence: 0.95
created: 2026-07-31
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/recall.py
  - path: plugin/tests/test_recall_idf_gate.py
evidence:
  - commit: c2ee956
  - note: "#470 stage-1 replay A/B over 215 real prompts. Arm A (gate off) injected 344 rows; arm B (gate ON at threshold 10) injected 374 — the gate ADDED 30 injections. Of the 123 arm-B-only injections, 86 were open questions, i.e. the exempt class. Tightening the threshold made the inversion monotonically worse."
expires:
  condition: "suggest() stops exempting any row class from the mass gate (no pinned / open-question passthrough), so a freed slot can no longer be filled by an exempt row"
  review_after: 2027-02-01
status: candidate
---

In `suggest()` the #470 idf mass gate is applied at the very BOTTOM, as a
filter over the already-selected `out` list, and it shrinks that list below
`limit` without pulling in a replacement. Two things there look like bugs and
are not: (1) `low_mass` is computed up in the pass-1 connection scope but not
read until after ranking and the one-per-session loop — the split exists only
because the df lookup must share the caller's single connection, and nothing
in between may read it; (2) the filter leaves a hole instead of backfilling.

Both are deliberate, and the reason is measured, not aesthetic. v1 (c2ee956)
did the obvious thing and skipped gated rows during scoring. Every skip freed
a slot, the next candidate was promoted into it, and the gate's own exempt
class — pinned standing rules and still-open questions — was first in that
queue. The gate therefore RECRUITED the rows it was only meant to spare: over
215 replayed real prompts, arm A injected 344 rows and arm B 374 at threshold
10, with 86 of the 123 B-only injections being open questions. A noise gate
that raises injections inverts its own purpose, and tightening the threshold
made it worse rather than better.

What a future editor must do instead: leave the mass computation where it is
and keep the filter post-selection. Do NOT "optimise" it into the scoring
loop, do NOT add a backfill so results reach `limit`, and do NOT reorder the
survivors. The invariant is that gate-on output is a SUBSEQUENCE of gate-off
output — same candidates, same ranking, same cap, minus zero or more rows.
It is pinned by `test_gate_on_output_is_a_subsequence_of_gate_off_output`,
`test_gate_frees_a_slot_without_backfilling_it`, and
`test_gate_never_promotes_an_exempt_sibling` in
`plugin/tests/test_recall_idf_gate.py`; if you are deleting or relaxing one of
those three, you are re-opening this scar.
