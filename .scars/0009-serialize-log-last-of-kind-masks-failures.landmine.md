---
id: 9
type: landmine
title: Reasoning over serialize.log with last-of-kind logic masks per-session buried failures
severity: high
confidence: 0.9
created: 2026-06-30
authors: ["claude-code", "kibukx"]
anchors:
  - pattern: "_parse_serialize_log|last[_ ]?result"
evidence:
  - pr: 0
  - note: "lab commit 5dbee88 (pre-public-history archive; squash-merge of lab PR #73)"
expires:
  condition: "serialize.log becomes per-session (one file per session) instead of a single global append-only ledger"
  review_after: 2027-06-30
status: active
---

`serialize.log` (`config.log_dir()/serialize.log`) is a SINGLE GLOBAL append-only
ledger shared across every session and project. Lines from overlapping sessions
interleave. Any code that reads it with "last of each kind" or "the last result
line decides" logic will MASK a real failure the moment a different, later
session succeeds.

Concretely (the bug fixed in this PR): close session A (serialize errors), then
session B (succeeds). Log order is spawn-A, error-A, spawn-B, success-B. Both
`status` (`_parse_serialize_log`, which pairs nothing) and `heal` ("the last
result line decides") then see B's success and treat A as fine — A's checkpoint,
though healable with its transcript still in the log, is silently and
permanently lost.

The fix was a PER-SESSION model: attribute every line to its session id (spawn
regex group, success checkpoint-path stem, error transcript stem), and use the
checkpoint STORE (`store.read_checkpoint(sid)`), not the last log line, as ground
truth for "still lost." See `_session_ledger` / `_outstanding_failures` /
`_compute_outstanding` in cli.py.

If you add ANY new consumer of serialize.log (a metric, a health check, a new CLI
surface), do NOT use last-of-kind or last-N-line heuristics for per-session
questions. Attribute by session id and confirm loss against the store. `now`-based
age must come from the SPAWN line (result lines carry no timestamp). Note also the
200-line tail bound: a failure whose lines scrolled past the tail is invisible by
design — acceptable, but don't assume the log is complete history.
