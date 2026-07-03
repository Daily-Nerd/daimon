---
id: 4
type: landmine
title: LLM judge per-claim grounding is noise-dominated without forced verification
severity: high
confidence: 0.85
created: 2026-06-10
authors: ["claude-code", kibukx]
anchors:
  - path: research/experiments/track-a/
  - pattern: "grounded|FMR|judge.{0,40}claim"
evidence:
  - note: "original evidence commit d444109 lost to squash-merge history; corroborated by research/LOGBOOK.md 2026-06-12 H1 holdout entry (every miss/dispute adversarially verified then hand-reviewed)"
expires:
  condition: "judge harness enforces search-before-grade for all grounded:false verdicts"
  review_after: 2027-06-10
status: active
---

During the 2-cycle degradation test, independent Haiku judges grading the SAME
reconstruction against the SAME transcript contradicted each other on identical
facts. Commit `d444109`: one judge grounded, another ungrounded — it has 2 hits
in the transcript. A strict judge marked an entire late-transcript block of S1
claims false (7 claims) because it never read the transcript tail. Hand-grep of
every disputed claim showed ALL apparent cycle-2 confabulations except one were
judge errors.

The landmine: per-claim FMR deltas between runs judged by different prompts (or
even the same prompt, different invocations) are NOT comparable. A "stricter"
judge prompt inflates FMR with misses, not catches. Conclusions about
confabulation drawn from single-judge grades at this granularity are unsafe.

What works: instruct the judge to grep the transcript for each claim's key
terms BEFORE it may grade `grounded: false` — absence must be confirmed by
search, not recall. The one judge so instructed (S3 cycle-2) produced 50/50
clean grades. For any FMR delta that matters, hand-verify the disputed claims;
they are few.
