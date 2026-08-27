---
id: 0
type: deadend
title: benchmark/.cache warm-looking legacy entries are unreplayable; offline replays must use .work stores
severity: medium
confidence: 0.9
created: 2026-08-26
authors: ["claude-code"]
anchors:
  - path: plugin/tests/bench/cache.py
  - pattern: "cache\.get|CheckpointCache"
evidence:
  - note: "2026-08-26 ungated-arm replay: 7521 of 7723 entries in benchmark/.cache are pre-#343 raw checkpoints (no envelope); cache.get counts every one as legacy_misses and returns None"
expires:
  condition: "the .cache is re-warmed with #343 envelope entries (each carries served_model), or the legacy entries are purged"
  review_after: 2027-02-26
status: candidate
---

Tried to build a zero-LLM benchmark replay on top of the serialized-checkpoint
cache: 7723 entries, 42MB, looks fully warm. Dead end. 7521 of them are
pre-#343 raw checkpoint dicts with no `{"served_model": ..., "checkpoint": ...}`
envelope, and `cache.get` refuses those by design (the #343 poison guard:
unattributable producer, `legacy_misses`). The remaining 202 envelopes carry
`served_model: null` (command backend), replayable only into a receiptless run.
So a "cache-only" run with the current harness re-pays the LLM call for nearly
every session — exactly what an offline replay must not do. Do not weaken the
guard to admit legacy entries; it exists because a gateway silently substituted
models mid-run (scar 0032 territory). The working substrate for offline replay
is `benchmark/.work/<qid>/` (written checkpoints + recall.db from completed
runs): copy the store, rebuild the index, run `recall.search` against the copy.
Proven 2026-08-26: reproduced interim-317-baseline-first54.json exactly.
