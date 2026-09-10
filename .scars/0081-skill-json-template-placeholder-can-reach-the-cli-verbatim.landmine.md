---
id: 81
type: landmine
title: a skill's example JSON template value can reach the CLI byte-for-byte unfilled, not just wrong
severity: high
confidence: 0.85
created: 2026-09-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: skills/
  - path: plugin/daimon_briefing/cli/__init__.py
  - pattern: "write-checkpoint"
evidence:
  - pr: 983
  - note: #983 round 1 shipped write-checkpoint --session, assuming a host without a live session id would still get a model-invented, at least locally-unique, session_id in the JSON body (the pre-fix behavior). Round 2 review verified with the real CLI on a host with no --session available: a model that pipes the skill's own example template through with the placeholder field UNFILLED writes the literal text \"introspection-<short-unique-id>\" to disk as a session_id. Because that string is a CONSTANT (not derived from anything per-invocation), every subsequent /daimon-end write on that host, in ANY project, landed at the exact same per-session file path and overwrote the PRIOR provisional outright, worse than the original bug (which at least produced distinct, if wrongly-linked, ids).
expires:
  condition: "the skill format gains a templating mechanism the model cannot pipe through unfilled (e.g. the CLI generates and returns the id, rather than asking for one in the request body)"
  review_after: 2027-03-08
status: active
---

Do not assume a model reliably replaces a placeholder shown inside a skill's
example JSON (`"session_id": "introspection-<short-unique-id>"`) before
piping that JSON to a CLI. A model can, and in practice does, forward the
literal example text unchanged, angle brackets and all. If a field like this
also has to be UNIQUE per invocation for downstream identity logic (as
`session_id` is for corroboration linking, #983), a literal unfilled
placeholder is not merely wrong, it is a constant that collides across every
future invocation, silently overwriting prior state each time.

The fix pattern: the CLI receiving the value must independently validate its
SHAPE (blank, missing, or containing template markers like `<`/`>`) and
substitute a freshly generated value when it looks unfilled, rather than
trusting that a schema-shaped request implies a model actually replaced
every placeholder in it. Never rely on prose instructions alone ("leave it
as shown, the CLI replaces it") to guarantee a downstream consumer treats an
unfilled field correctly; when identity or uniqueness is load-bearing,
generate it in code, on the receiving end, whenever the caller cannot
reliably supply the real thing.
