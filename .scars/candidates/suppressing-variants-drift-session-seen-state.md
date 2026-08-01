---
id: 0
type: landmine
title: Any suppressing replay variant drifts session seen-state — later prompts in the same session no longer compare A-vs-B cleanly
severity: medium
confidence: 0.85
created: 2026-08-01
authors: ["claude-code"]
anchors:
  - path: research/experiments/recall-replay-ab/
  - pattern: "return \[\]"
evidence:
  - note: "#483 run-04: 38 of 166 diff prompts carried b_only injections that arm A never produced — impossible for a pure prompt gate whose pass-through arm is 'identical to A'. Traced (prompt_idx 214): when arm B suppresses an earlier prompt's injections, it never marks the origin session as seen, so a later pass-through prompt in the same session faces a larger candidate pool than arm A's. Session-cooldown-layer analog of #470's slot-promotion trap."
expires:
  condition: "the replay rig gains a mode that replays arm B against arm A's seen-state (or documents per-arm seen-state as the intended semantics in README)"
  review_after: 2027-02-01
status: candidate
---

The replay rig maintains per-arm seen/cooldown state. A variant that returns
fewer rows than arm A (any suppressor) therefore accumulates DIFFERENT
seen-state, and every later prompt in the same session diverges from arm A
for reasons unrelated to the hypothesis — b_only rows appear on prompts the
variant claims to pass through unchanged. This is not a bug in the rig (per-
arm state is arguably the honest simulation of shipping the variant), but it
breaks the naive reading "diff rows = the gate's direct effect." When
interpreting a suppressing variant's diff: partition b_only rows by whether
an earlier same-session prompt was suppressed, before attributing them to
the hypothesis. #483's b_only rows judged ~equal quality (32% relevant) so
the verdict survived; a closer call would not.
