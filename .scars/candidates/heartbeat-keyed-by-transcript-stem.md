---
id: 0
type: landmine
title: Serialize heartbeats are keyed by transcript STEM, never a host payload session id
severity: high
confidence: 0.9
created: 2026-08-29
authors: ["claude-code"]
anchors:
  - path: hook/_daimon_hook_lib.py
  - path: plugin/daimon_briefing/_hooks/_daimon_hook_lib.py
  - pattern: "_in_flight_stems|touch_heartbeat|heartbeat_age"
evidence:
  - commit: 681c234
  - pr: 814
  - note: "#813 field trace: hook logged `spawned serialize for 01a04bd2-...` while the checkpoint landed as `rollout-2026-08-28T22-41-50-01a04bd2-....json`"
expires:
  condition: "cli/__init__.py stops deriving session_id from the transcript filename (`session_id = path.stem`), or hooks start passing their session id through to `daimon serialize`"
  review_after: 2027-02-28
status: candidate
---

`daimon serialize <transcript>` derives its session id from the FILENAME
(`cli/__init__.py:233`, `session_id = path.stem`), and that derived id is what
`ledger.touch_heartbeat` stamps into `<log_dir>/heartbeats/`. A host hook's
payload `session_id` is a DIFFERENT string. Codex is the clearest case: the
payload carries `01a04bd2-...` while the transcript is
`rollout-2026-08-28T22-41-50-01a04bd2-....jsonl`, so the stem and the payload
id share a substring and are not equal.

Any liveness check written against the payload `session_id` therefore matches
no heartbeat, ever. It does not raise, does not log, and does not fail a test.
It reads as an implemented guard and is a no-op. This is the obvious way to
write it, because the surrounding hook code has `session_id` in scope and the
transcript path is the less convenient value.

`_in_flight_stems()` returns STEMS for exactly this reason, and its two
consumers both respect it: `sweep_orphans` compares against `candidate.stem`,
and `_serialize_in_flight` (added by PR #814) does `Path(transcript_path).stem`.
Keep it that way. If you add a third consumer, key it on the transcript path
you are about to hand to the CLI, never on whatever the host called the session.

Pinned by `test_spawn_serialize_keys_the_guard_on_the_transcript_stem` and
`test_codex_session_end_skips_when_a_stop_serialize_is_in_flight`, both of
which deliberately use a payload id that differs from the stem. If you change
the key, those are the tests that should go red.
