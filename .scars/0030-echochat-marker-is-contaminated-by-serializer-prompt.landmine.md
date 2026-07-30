---
id: 30
type: landmine
title: The bench EchoChat fake echoes bare "marker", not the per-session token — its topic text does NOT discriminate sessions by content
severity: medium
confidence: 0.85
created: 2026-07-29
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/tests/test_bench_adapter.py
  - pattern: "endswith.*marker"
evidence:
  - commit: 5e0f6b2
  - note: #405 forbidden-hit cases: assumed the gold checkpoint's topic would contain the per-session marker token (e.g. thinkpadmarker); it contained the bare word 'marker' instead, so a forbidden string keyed on the token matched nothing
expires:
  condition: "EchoChat is changed to emit a token that cannot collide with the serializer prompt (e.g. a UUID-shaped marker), or serialize_strict stops prepending prompt text to the transcript blob the fake inspects"
  review_after: 2026-12-01
status: active
---

EchoChat (test_bench_adapter.py) picks its marker with
`next(w for w in blob.split() if w.endswith("marker"))`, where `blob` is the
message list serialize_strict hands the chat backend. serialize_strict PREPENDS
its own system/instruction text to that list, and that text already contains a
word ending in "marker" — so `next(...)` returns the bare "marker" from the
prompt, never the per-session `thinkpadmarker`/`hellomarker` token from the
transcript. Every session therefore serializes to the IDENTICAL active_topic
text "session about marker".

Consequence: `test_run_question_retrieves_the_gold_session` passes for the wrong
reason — recall_at_5 == 1.0 because BOTH sessions carry the same text and both
land in the top-k, not because the marker discriminated the gold session. Any
new test that relies on EchoChat's output being session-distinct (e.g. keying a
forbidden string or a recall assertion on the token) will silently mis-fire:
the string is never in the brief, so a leak test reads clean and a recall test
reads as a coincidental hit. For content-distinct checkpoints, use a dedicated
fake that returns a fixed, prompt-collision-proof string (see SecretChat added
in #405), or read back the written checkpoint text rather than assuming the
token survived.
