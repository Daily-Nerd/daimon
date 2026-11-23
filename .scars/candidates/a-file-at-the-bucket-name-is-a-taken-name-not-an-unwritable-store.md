---
id: 0
type: landmine
title: A file at a bucket's name is a taken name now, so it no longer simulates an unwritable store
severity: medium
confidence: 0.85
created: 2026-11-22
authors: ["claude-code"]
anchors:
  - path: plugin/tests/test_requests.py
  - path: plugin/tests/test_world_outcomes.py
  - path: plugin/daimon_briefing/identity.py
evidence:
  - commit: ee0a99e
expires:
  condition: "birth stops treating an existing entry at the seed name as taken"
  review_after: 2027-05-22
status: candidate
---

Several writer tests made the bucket directory path a FILE to prove "an
unwritable bucket degrades to False instead of raising". With the real
resolver a name that exists as anything (`lexists`) is TAKEN, so the claim
walks on and births `<seed>-<6 hex>` beside the file: the write succeeds in a
different bucket and the test fails the other way round.

The unwritable store is the checkpoint ROOT being a file (nothing can be born
under it), which is what those tests assert now. A genuine write failure of
the claim itself (the stage cannot be made) is deferred, not raised: the
writer's own `ensure_bucket` hits the same `OSError` inside its handler, so
the contract of "unwritable store returns False" is unchanged.

Tests that simulate "unknown project" by stubbing `store.project_slug` also
stop working for absolute paths: the owner is `store.bucket_of`, which reaches
the resolver, so stub that.
