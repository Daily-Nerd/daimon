---
id: 89
type: landmine
title: recall.suggest admits one row per origin session, so a slot-table test seeded from one checkpoint never exercises a non-lead slot
severity: medium
confidence: 0.9
created: 2026-09-14
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/recall.py
  - path: plugin/daimon_briefing/cli/__init__.py
evidence:
  - note: #1030, widening the lead recall slot: the first draft of the slot-table test seeded two long items inside ONE checkpoint and recall-inject emitted a single line.
expires:
  condition: "suggest() stops deduplicating by session_id in its final ranking pass"
  review_after: 2027-03-14
status: active
---

`recall.suggest` ends with a per-session filter: the ranked rows are walked and
any row whose `session_id` was already used is skipped, so one origin session
contributes at most ONE candidate no matter how many of its items matched. The
gate lives at the very end of `suggest`, far from the injection budget it
interacts with, and nothing on the `recall-inject` side hints at it.

Consequence for tests: seeding two matching items into one checkpoint and
running `recall-inject` yields one injection line, not two. The two-slot
behavior in `_cmd_recall_inject` (the #1030 lead/non-lead width table, the
`_INJECT_BUDGET` cap, the #451 content dedup promoting the next candidate)
needs candidates from at least two distinct origin sessions to fire at all.

Why this is a landmine and not just a surprise: a test that only asserts the
LEAD slot's property passes vacuously against a one-session fixture, because
the assertion it would have failed on lives in a slot that was never rendered.
The #1030 test survives only because it asserts the full list of widths
(`[_LEAD_WIDTH, _SLOT_WIDTH]`) and therefore also pins the line COUNT. Any
future test of per-slot behavior must do the same, and must write one
checkpoint per item.
