---
id: 20
type: deadend
title: Exact-shape guards on host transcript rows (len(obj)==N) fail silently on schema widening — key on a discriminating FIELD instead
severity: high
confidence: 0.85
created: 2026-07-04
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/transcript.py
evidence:
  - pr: 71
  - note: review of db92fbd traced the loss mode; fix 87dd98b's RED test reproduced it (schema-widened Cascade row → all planner_response turns dropped, user-only transcript, zero errors)
expires:
  condition: "host-format detection in transcript.py moves to explicit per-host format declarations instead of shape sniffing"
  review_after: 2027-07-04
violation: "len\(\w+\)\s*==\s*\d"
status: active
---

Tried in the Windsurf Cascade parser (#70): gating the dedicated branch on
`len(obj) == 3` because every field-sampled row had exactly three keys. Killed
in review. Failure mode: any host schema widening (one added key, e.g. a
timestamp) silently disables the whole branch; rows then fall through to the
best-effort parser, which knows `user_input` but not `planner_response` — so
assistant turns vanish, the transcript parses "successfully" as user-only, and
nothing errors or dumps. Field-observed shape is ONE sample of a moving format
(Windsurf docs already lied 3× in this arc; Codex docs say their format is
unstable). Detect host formats by a DISCRIMINATING FIELD the branch actually
consumes (Cascade: `"status" in obj`), never by exact row arity or full-shape
match — a widened row must degrade to a no-op for the guard, not to silent
data loss downstream.
