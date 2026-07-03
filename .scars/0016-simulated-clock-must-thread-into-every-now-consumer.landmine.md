---
id: 16
type: landmine
title: Simulated-time stamps freeze decay math unless the clock is threaded into every now-consumer
severity: high
confidence: 0.9
created: 2026-07-02
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/experiments/multicycle/
  - pattern: "briefing\.build\b"
evidence:
  - commit: 510787d  # fix squashed into PR #134
  - note: live run-01 killed 2026-07-02 — 6 cycles of spend invalidated
expires:
  condition: "briefing.build / scoring.effective_weight require an explicit now (no wall-clock default)"
  review_after: 2027-01-01
status: active
---

The multicycle experiment stamps checkpoints with a simulated calendar
(base + 1 day per cycle) but `briefing.build(cp)` defaults `now` to wall
clock. `scoring._age_days` clamps negative ages to 0, so future-stamped
items read permanently "fresh" while backdated seed items froze at fixed
age — the #78 decay axis silently died, and distractor-arm budget ordering
preferentially dropped seed items, which would have FABRICATED a
crowding-out collapse finding (commit 35758c8 fixed it; run-01 was killed
and deleted). If you stamp simulated time onto checkpoints anywhere, you
must pass that same clock to EVERY time consumer (`briefing.build(...,
now=)`, anything calling `scoring.effective_weight`). Nothing crashes when
you forget — the numbers just quietly stop meaning what the experiment
says they mean. Grep for default-now calls before trusting any
time-progression result.
