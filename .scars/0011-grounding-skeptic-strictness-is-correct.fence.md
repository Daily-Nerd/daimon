---
id: 11
type: fence
title: The strict grounding skeptic looks over-strict (low rescue rate) but is correctly calibrated — do not soften it
severity: high
confidence: 0.85
created: 2026-06-29
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/experiments/track-a/scoring/grounding_skeptic.py
  - path: research/experiments/track-a/scoring/grounding_fixture.json
  - pattern: "verify_negative|GROUNDED IS STRICT|skeptic"
evidence:
  - pr: 59
  - note: "lab commit fcaaffd (pre-public-history archive; squash-merge of lab PR #59)"
  - note: See .scars landmine #4 (per-claim judge deltas are noise-dominated without forced verification).
expires:
  condition: "The fixture is re-labeled against full transcripts and the skeptic still misses >25% of TRUE judge_errors that are single-sentence (not multi-turn) assertions."
  review_after: 2027-06-29
status: active
---

The strict skeptic's prompt defines grounding as "the transcript ASSERTS or clearly
ENTAILS the claim" and rejects token-reuse / over-extrapolation. On the first fixture
it rescued only 3/8 judge_errors, which LOOKS badly under-calibrated and invites
softening the prompt to be more lenient. DO NOT.

When the 5 apparent misses (r79, r2, r20, r65, r76) were adjudicated against the FULL
H3/H4 transcripts (forced whole-transcript reads + human adjudication, PR #59), 4 of
the 5 were bad hand-labels — the skeptic was RIGHT to call them confab (over-extrapolation,
ref/entity conflation, externally-imposed meta-claims). Corrected fixture (4 judge_error
+ 9 confab) gives the skeptic 9/9 confab precision and 3/4 rescue. The single genuine
miss (r2) is a multi-turn temporal entailment, and erring conservative there is the SAFE
direction for an FMR gate (better to flag a true claim than pass a confab).

Future editor: if you see a "low rescue rate" and want to loosen the skeptic prompt,
first re-verify each missed claim against the full transcript. The low number is almost
certainly label noise, not over-strictness. Softening trades away the 9/9 confab precision
— the whole point of the skeptic — to chase misses that are mostly mislabeled. This is the
same trap as .scars landmine #4.
