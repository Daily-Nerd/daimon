---
id: 37
type: deadend
title: Prompt-stance surface markers do not predict recall relevance — gating injection on question-vs-statement shape refuted (#483)
severity: medium
confidence: 0.9
created: 2026-08-01
authors: ["claude-code", "Kibukx"]
anchors:
  - path: research/experiments/recall-replay-ab/variants.py
  - path: plugin/daimon_briefing/recall.py
  - pattern: "classify_stance|question.?shaped|statement.?shaped"
evidence:
  - note: #483 pre-registered run (run-04-stance-gate, 2026-08-01, same 342-prompt corpus as #470's run-03; arm A reproduced run-03's 344 injections exactly). Holdout blind judging: relevant rate in rows the gate DROPPED = 38.5%, vs 39.9% in arm A's full pool and 39.4% in what the gate kept — statistically indistinguishable. Both pre-registered ship clauses failed; kill condition met. Classifier itself was reliable (spot-checked); the markers simply do not carry relevance signal.
expires:
  condition: "a stance signal richer than surface markers (e.g. trained intent classification) is tested and shows the dropped-row relevant rate materially below the kept-row rate"
  review_after: 2027-02-01
status: active
---

Hypothesis (#483, from the remnant collision-detector critique): overlap
measures shared vocabulary, not epistemic stance, so gating recall injection
on question-shaped vs statement-shaped prompts should cut fresh-tangential
noise. Built as a deterministic surface classifier (interrogative openers,
help phrases, trailing "?"), run on the #472 replay instrument, blind-judged
on the holdout split. REFUTED: relevance in suppressed rows equaled relevance
in kept rows — statement-shaped prompts need memories exactly as often as
question-shaped ones. Do not rebuild this as a surface-marker gate; the
mechanism claim ("approaching vs mentioning") may still be true, but surface
stance is not a usable proxy for it. Second consecutive selection-shaping
refutation after #470 (rarity); both died on the same lesson: judge what the
gate removes, not what it keeps.
