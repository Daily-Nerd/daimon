---
id: 104
type: deadend
title: Appending session-context terms to the recall query adds coincidence, not precision, four arms refuted
severity: medium
confidence: 0.85
created: 2026-09-24
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: research/experiments/recall-replay-ab/variants.py
  - path: plugin/daimon_briefing/recall.py
violation: "salient_terms\(.*prev_assistant|active_topic.*salient_terms"
evidence:
  - note: pre-registered replay A/B on the maintainer's prompt corpus, 2026-09-24: arms prev, topic, both and placebo_prev all refuted on the holdout split, two blind judges in consensus; per-slot precision of the new rows fell below the rows they displaced in every arm, and prev matched the placebo
expires:
  condition: "a conjunctive context variant (prompt-term hit AND context-term hit) is measured and passes the pre-registered rule"
  review_after: 2027-03-01
status: active
---

Do not widen the recall query by appending salient terms pulled from session
context (the previous assistant turn, the checkpoint active topic, or both)
to try to raise delivery precision. Four arms were built, pre-registered,
and replayed against the maintainer's real prompt corpus: `prev`, `topic`,
`both`, and a null control `placebo_prev` fed with a different session's
context. All four were refuted on the pre-registered holdout, two blind
judges reaching consensus; the maintainer holds the run record.

The placebo scored the same as the real context, so the extra terms were not
the source of any signal. Recall's query is OR-joined full-text search with
a two-term overlap floor, and a dozen extra terms cross that floor by
coincidence almost as easily as by a genuine shared reference, reviving
prompts that had gone silent under the shipped query for good reason.

A future input-side variant on this instrument must narrow the match instead
of widening it, for example requiring a hit on a prompt term AND a hit on a
context term, never either alone.
