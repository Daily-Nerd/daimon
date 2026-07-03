---
id: 10
type: landmine
title: LLM judges silently grade the wrong dimension — grep-verify catches absence errors, not category errors
severity: high
confidence: 0.8
created: 2026-06-12
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/experiments/track-a/
  - pattern: "stale|staleness|adversarial.{0,40}verif|overturn"
evidence:
  - note: H1 holdout judging 2026-06-12, LOGBOOK.md entry; runs/H1/haiku-judge/*-holdout.json hand_review blocks (local)
  - note: v2 revalidation 2026-06-12: staleness judge flagged 5 more (H2 gt9, H4 gt4/5/7/10) — all 5 wrong-dimension errors again; 10/10 false flags across v1+v2. Recall verifier: 3 of 8 overturn claims rejected in hand review (wrong-item evidence, partial-component evidence). runs/H*/haiku-judge/ hand_review blocks (local)
expires:
  condition: "judge prompts require a per-verdict evidence quote that is programmatically checked against the source AND a one-line restatement of the dimension being graded"
  review_after: 2027-06-12
status: active
---

Scar 0004 established that grounded:false verdicts need forced search. The H1
holdout run (2026-06-12) showed a failure mode grep-verify cannot catch: the
judge answers a DIFFERENT question than asked. The staleness judge flagged 5
items "stale" — every one was a judge error; it had graded "is this topic
uncertain in the reconstruction" instead of "is this pinned to a superseded
in-session state," while missing the one genuinely stale item (gt1). The
adversarial recall verifier, told to find evidence an item WAS recalled, cited
real reconstruction text that belonged to a DIFFERENT item on 3 of 5 overturn
claims — verbatim-quote checking passed, item-identity checking did not exist.

What a future judge harness must do: (1) verbatim-check evidence quotes against
the source (already done), (2) ALSO verify the evidence actually concerns the
item being graded — wrong-item citations pass the verbatim check, (3) for
dimension-graded passes (staleness, epistemic state), require the judge to
restate what would make the verdict TRUE for this item before grading; hand-
review every positive flag, not just disputes. Aggregate numbers from
single-pass dimension judges are not publishable without this audit.
