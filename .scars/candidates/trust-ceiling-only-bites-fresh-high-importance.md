---
id: 0
type: landmine
title: The #408 trust ceiling only clips FRESH, high-importance items — stale ceiling tests tie misleadingly
severity: medium
confidence: 0.9
created: 2026-07-29
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/scoring.py
  - pattern: "trust_ceiling|TRUST_CEILING"
evidence:
  - pr: 408
  - note: "recall ceiling test tied verbatim vs inferred at 39d overdue (raw ~0.30 < 0.7 lid) until now was aligned to first_seen so raw ~1.0 clipped the inferred item"
expires:
  condition: "TYPE_RULES reshaped so overdue-escalated inferred items can exceed the inferred ceiling, or the ceiling becomes a multiplicative discount instead of a min() clamp"
  review_after: 2026-12-01
---

effective_weight applies the trust ceiling as `min(weight, ceiling)`. The lid
therefore only changes anything when the RAW weight exceeds it. With the
current TYPE_RULES, the raw product base*recency*decay*boost tops out near 1.0
for a fresh, importance-10, non-escalated item, and — crucially — an
overdue-escalated open_question maxes around 0.8 (recency and decay have already
fallen by the time the boost engages). So the inferred lid of 0.7 bites ONLY in
the fresh / high-importance corner; a stale or mid-importance inferred item is
already below 0.7 and the ceiling is a no-op for it.

Consequence for tests: a recall/ordering test that seeds an inferred vs a
verbatim item with equal importance but STALE first_seen will see both raw
weights sit under 0.7, so the ceiling never separates them and they TIE — the
assertion fails for a reason that looks like "trust isn't wired in" when it
actually is. To exercise the lid you must drive the raw weight above 0.7:
fresh age (align the injected `now` to first_seen) and high importance. Do NOT
"fix" a tying test by lowering the ceiling blindly — check whether the raw
weight even reaches the lid first. Same trap applies to anyone retuning
TYPE_RULES: verify the escalation product still cannot cross the inferred
ceiling, or the structural guarantee (#408) silently weakens.
