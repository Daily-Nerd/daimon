---
id: 79
type: fence
title: after #983, a provisional's per-session file only reads as a provisional until its OWN reconstruction lands
severity: low
confidence: 0.8
created: 2026-09-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/capture.py
  - pattern: "_origin_on_disk"
evidence:
  - pr: 983
  - note: #983 fix: write-checkpoint --session now stamps the SAME real session id on a /daimon-end provisional and its later SessionEnd reconstruction, so store.write_checkpoint's path = _contained_path(d, session_id) makes the reconstruction overwrite the provisional's own per-session file in place. capture._origin_on_disk reads that same file to decide whether an origin is a provisional (source == \"introspection\"). Before the session's own reconstruction runs, the file answers 'yes, provisional' (refuses); the instant the reconstruction writes, the same path answers 'no' (source is unset), because it is a different checkpoint occupying the same filename. Verified in test_a_provisional_and_its_own_reconstruction_never_corroborate: a THIRD session's later corroboration of the same claim succeeds only because, by the time it runs, the origin session's reconstruction has already overwritten the file.
expires:
  condition: "the per-session file gains a durable 'this session was ever provisional' marker that survives being overwritten by its own reconstruction"
  review_after: 2027-03-08
status: active
---

Do not assume `capture._origin_on_disk`'s provisional check (source ==
"introspection") is a permanent property of a session id — after #983, it is
a property of whichever checkpoint currently occupies that session's
per-session file, and that file gets overwritten in place the moment the
session's own SessionEnd reconstruction runs (same session id, same
`_contained_path`). A corroboration emitted in the narrow window between a
provisional write and its own reconstruction sees "provisional, refuse"; the
identical origin, checked moments later after the reconstruction lands, sees
"not a provisional, allow." This is intentional (a session that actually
finished should be able to back a corroboration once its real transcript
lands) but it means a test or a future reader reasoning about "was session S
ever provisional" from `read_checkpoint(S)` alone gets a TIME-DEPENDENT
answer, not a fact about S's history. If a durable answer is ever needed
(e.g. an audit trail), it has to come from somewhere other than the
overwritable per-session file — the reconstruction has no field today that
records "this session was provisional at some point."
