---
id: 18
type: landmine
title: grounding screen's safety invariant depends on the salient_tokens drop-list
severity: medium
confidence: 0.8
created: 2026-06-29
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/experiments/track-a/scoring/grounding_screen.py
  - pattern: "STOPWORDS|salient_tokens|screen_negative"
evidence:
  - note: "lab commit fef27b5 (pre-public-history archive; squash-merge of lab PR #51)"
expires:
  condition: "the screen stops auto-confirming the absent bucket (e.g. a skeptic re-judge is added over the ABSENT bucket — present-bucket adjudication alone does NOT expire this)"
  review_after: 2027-06-29
status: active
---

`screen_negative` returns `absent` (judge reliable → keep `grounded:false`, i.e.
CONFIRM a confabulation) only when NO salient token of the claim appears in the
transcript. Safety rests entirely on `salient_tokens` dropping the right words:
over-drop and a claim whose ONLY real support-token is on the drop-list screens
`absent` wrongly — silently confirming a judge false-negative, the exact 69%
error this screen exists to kill.

This was load-bearing for r93 (commit 2e7d742): the screen keeps it `absent`
because "constraints" is dropped as a generic noun and its trait tokens
(pragmatic/methodical/iterating) are truly absent. Verified empirically that none
of the 9 judge_errors depend on any dropped noun — each has many specific present
tokens — so the invariant holds for the RIGHT reason, not by luck. But that
verification is only against H3/H4.

Future editor: before trusting this screen on NEW sessions, re-confirm the
invariant on that data (no `judge_error` may screen `absent`) — do NOT expand
STOPWORDS to make a case pass. The robust fix is Slice 2: a skeptic re-judge of
the `present` bucket, so `absent` is no longer the sole confirmation path. Until
then, `absent` is a deterministic heuristic, not proof of confabulation.
