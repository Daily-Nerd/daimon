---
id: 2
type: landmine
title: Concurrent probe_d007.py runs race on runs/ — no locking
severity: medium
confidence: 0.9
created: 2026-06-09
authors: [claude, kibukx]
anchors:
  - path: research/experiments/track-a/probe_d007.py
  - pattern: "score.json"
evidence:
  - "2026-06-09: background run + user foreground run started ~3min apart; idempotency check (score.json exists?) raced, both re-serialized arms B/C, ~116k duplicate kimi tokens, interleaved artifacts in runs/S2/probe-d007/"
expires:
  condition: "probe gains a lockfile or unique run-id dirs"
  review_after: 2027-06-09
status: active
---

The probe's idempotency is check-then-write on `score.json` with no lockfile.
Two concurrent invocations (e.g. agent background + user terminal) both pass
the check before either writes, re-spend full serialize tokens, and interleave
artifacts in the same arm dirs — last writer wins, provenance murky.
Don't run the probe twice concurrently; if automating, add a lockfile or
unique run-id dirs first.
