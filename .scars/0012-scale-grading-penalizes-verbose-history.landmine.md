---
id: 12
type: landmine
title: state-benchmark stale-substring grader fakes a "prose loses" signal — answer verbosity, not memory fidelity
severity: high
confidence: 0.85
created: 2026-06-27
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/memory-backend/benchmark/state/grade.py
  - pattern: "ANSWER_PROMPT|asserts_stale|is_override"
evidence:
  - commit: bc1114b  # fix squashed into PR #41
  - note: First 2K scale smoke: prose override 0.167 / staleness 0.833 vs CSL 1.000 — looked like prose collapses under noise.
expires:
  condition: "grading scores semantic current-state instead of substring-matching stale tokens"
  review_after: 2026-12-27
status: active
---

`grade_state` fails an override probe if the answer contains ANY stale token as a
substring. That couples the score to answer VERBOSITY, not memory fidelity: a
correct prose answer like "20% (down from 30%)" or "Rust ... Go elsewhere" mentions
the history and is graded STALE, while CSL's terse "20%"/"Rust" passes. This faked a
dramatic "prose collapses" result (smoke) that was pure artifact — prose memory held
the state (gold_recall 1.0). Confirmed via the per-probe `answers.jsonl` audit trail.
Fix was forcing terse current-only answers in ANSWER_PROMPT (5a1bdb6). DO NOT trust a
prose-vs-csl gap without checking answers.jsonl for history-mention false-stales, and
keep the answer prompt terse. Structure wins false points here purely by being terse.
