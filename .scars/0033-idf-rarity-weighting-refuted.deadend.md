---
id: 33
type: deadend
title: Rarity/IDF weighting of recall matches is anti-correlated with relevance — measured and refuted, do not rebuild it
severity: high
confidence: 0.9
created: 2026-07-31
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/recall.py
  - path: research/experiments/recall-replay-ab/
evidence:
  - note: #470 pre-registered offline replay A/B, 237 blind-judged injection pairs from the maintainer's real prompt corpus. On the pre-registered HOLDOUT split arm B's per-slot precision was WORSE than arm A at every threshold: 17.3% / 16.9% / 15.9% / 12.3% at thresholds 6/8/10/12 vs A's 18.8%. The pre-registered zero-loss guard (no judged-relevant A injection lost) FAILED at every threshold.
  - note: Diagnostic: the gate drops judged-relevant items at a HIGHER rate than the items it keeps. At threshold 12, 26.0% of dropped injections were judged relevant vs 17.0% of kept ones. Summed term rarity is slightly ANTI-correlated with relevance in this corpus.
  - note: Earlier in-selection variant: over 215 replayed real prompts arm A injected 344 rows and arm B 374 at threshold 10 — the noise gate ADDED 30 injections — and 86 of the 123 B-only injections were open questions, i.e. the gate's own exempt class.
expires:
  condition: "a replay A/B run on a materially different corpus (different author, different project mix, or >10x the item count) shows summed-rarity weighting positively correlated with judged relevance under the same pre-registered criteria"
  review_after: 2027-02-01
status: active
---

Do not add IDF / rarity / TF-IDF weighting to recall selection. It was built,
pre-registered and measured against real replayed prompts, and it lost.

The idea is seductive: a match on two COMMON words looks like vocabulary
coincidence, so weight matched terms by rarity (`ln(n_items/df)` from a
per-project document-frequency table) and drop sessions whose matched terms
carry too little summed mass. Measured, it does the opposite of what it
promises. On the pre-registered holdout split arm B's per-slot precision was
below arm A's at EVERY threshold (17.3/16.9/15.9/12.3% vs 18.8%), the
zero-loss guard failed everywhere, and the gate dropped judged-relevant rows
at a higher rate than it kept them (threshold 12: 26.0% relevant among
dropped, 17.0% among kept). The reason is corpus-shaped and worth
internalising: the shared vocabulary that actually signals "I worked on this
before" IS the common working vocabulary of the project. The rare tokens are
disproportionately incidental — identifier fragments, one-off filenames,
tokens pasted out of logs. Rarity is not relevance here; it is closer to
noise.

Second, separate dead end, for anyone tempted to gate on some OTHER signal:
do not apply the gate DURING candidate selection. The first implementation
skipped gated rows while scoring, every skip freed a slot, the next candidate
was promoted into it, and the gate's exempt class (pinned standing rules and
still-open questions) was first in that queue. The gate RECRUITED the rows it
was only meant to spare and made recall louder — 344 injections in arm A vs
374 in arm B at threshold 10, 86 of the 123 B-only injections being open
questions, monotonically worse as the threshold tightened. Any future gate
must be a post-selection filter with no backfill, so gate-on output is a
SUBSEQUENCE of gate-off output. That trap is structural, not IDF-specific.

The instrument survived the hypothesis. Before betting on the next recall
scoring idea, run it through `research/experiments/recall-replay-ab/`: write
a variant, pre-register the criteria in the issue, replay, judge side-blind,
decide on the holdout. It costs minutes, and it is why this was caught before
shipping instead of after.

Anchoring note for whoever promotes this: scar anchor-liveness scanning only
reads the first 8 KB of a file, so a `pattern:` anchor aimed at something
deep inside `recall.py` (a ~1100-line module) reports false rot even when the
matching code is right there. Use `path:` anchors here, as above.
