---
id: 1
type: deadend
title: LLM-judge grounding must reference the transcript, never the answer key
severity: high
confidence: 0.95
created: 2026-06-09
authors: [claude, kibukx]
anchors:
  - path: research/experiments/track-a/probe_d007.py
  - pattern: "GROUNDING_JUDGE_SYS"
  - path: research/experiments/track-a/scoring/score.py
evidence:
  - commit: e665a43
expires:
  condition: "FMR scoring no longer uses LLM-as-judge"
  review_after: 2027-06-09
---

The first probe judge defined `grounded` as "supported by the ground truth
item list". This mismeasured FMR catastrophically: ground-truth keys
under-cover the transcript (29 items vs 51 reconstruction claims), so
surplus-but-TRUE detail (port numbers, PR #62, an `omitempty` json tag —
all real transcript facts) was flagged as confabulation. Arm A scored
"FMR 35%" on a checkpoint that human scoring put near 0%. Arm C's rich
recall looked like fabrication precisely because it recalled MORE than
the answer key contained.

Fixed in `e665a43`: grounding judge checks every claim against the
ORIGINAL TRANSCRIPT (chunked; grounded iff ANY chunk supports it). Live
rejudge collapsed armA FMR 25%→2.0%, armB 11.5%→4.7%.

Do not "simplify" the two-pass judge back to a single GT-referenced pass.
Recall is judged against GT (correct — that's what GT is for); grounding
is judged against the source transcript (the only complete record).
Answer keys measure coverage, transcripts measure truth.
