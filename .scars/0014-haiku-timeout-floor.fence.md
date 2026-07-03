---
id: 14
type: fence
title: DAIMON_TIMEOUT must stay >=420 — haiku real serialize/merge calls run 80-250s, not smoke-test fast
severity: medium
confidence: 0.85
created: 2026-06-13
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/config.py
  - pattern: "DAIMON_TIMEOUT"
evidence:
  - note: D-008 re-judge 2026-06-13 — DAIMON_TIMEOUT lowered 420->120 on a 3-4s trivial-prompt smoke test; H1 merge-level-2 and H3 judge then timed out after 3 retries. Restored to 420.
  - note: Corroborated in-tree: research/LOGBOOK.md 2026-06-13 entry (248s calls x3 retries) and research/experiments/track-a/rerun.py RECOMMENDED_TIMEOUT_S = 420.
expires:
  condition: "a generation model whose full-chunk serialize/merge calls reliably complete (first-token + stream) under a shorter ceiling"
  review_after: 2026-12-13
status: active
---

A trivial smoke test (e.g. "extract 3 fruits") returns in 3-4s on a
haiku-class gateway route. This is NOT representative. Real serialize on a
~1200-line chunk, and especially hierarchical merge calls, run 80-250s each
(chunk 2/6 = 249s, merges = 241-249s in the 2026-06-13 H1 run).
DAIMON_TIMEOUT is a TOTAL budget for the serialize work: the deadline is
computed at hook start and shared across ALL retry attempts, with per-attempt
socket timeouts capped to the remaining budget (hooks.py, plugin README).
Under budget semantics the floor argument is even stronger than under the old
per-call silence timeout: a 120s total budget cannot fit even ONE 250s call,
let alone a retry. Do NOT lower DAIMON_TIMEOUT below 420 to "fail fast"
just because smoke tests look instant. The rerun.py warning that cites 248s
checkpoints (RECOMMENDED_TIMEOUT_S = 420) is correct — heed it, don't dismiss
it as kimi-era.
