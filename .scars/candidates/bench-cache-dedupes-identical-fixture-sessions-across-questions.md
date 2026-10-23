---
id: 0
type: landmine
title: the bench checkpoint cache keys on transcript content, so identical fixture sessions across questions are cache hits, not serialize attempts
severity: low
confidence: 0.9
created: 2026-10-23
authors: ["Kibukx"]
anchors:
  - path: plugin/tests/test_bench_abort.py
  - path: plugin/tests/test_bench_model_pin.py
  - pattern: "haystack_sessions.*_turns\("
evidence:
  - note: 2026-10-23 #344 test_partial_failure_past_threshold_also_aborts expected 1 of 4 attempted serializes and got 1 of 3; q_0 and q_1 shared byte-identical sessions, so q_1's second session replayed from the cache q_0 had just filled
expires:
  condition: "cache_mod.cache_key stops hashing the message content, or run-level tests stop sharing one CheckpointCache across questions"
  review_after: 2027-04-23
status: candidate
---

`cache_mod.cache_key` hashes the session's messages (plus backend, model,
prompt version, carry, scene). It does not include the question id or the
session id. A run-level test that builds several questions from the same
`_turns(...)` content therefore serializes each distinct transcript ONCE for
the whole run: the second question's copies are `cached` tallies, never
`serialized` or `failed`, and never a call on the chat fake. Anything counted
over attempted serializes (the #344 failure gate, `chat.calls`, cost
accounting) shrinks silently; the test still runs green against the wrong
arithmetic until an exact count is asserted. Thread the question id into the
transcript words (`_turns(f"alpha {qid}")`) when a fixture spans questions, or
give each question its own cache dir. The dedupe is the cache doing its job
(a re-run pays only for sessions never seen), not a bug.
