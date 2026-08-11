---
id: 52
type: deadend
title: Passing the contract as a real system prompt does NOT stop transcript continuation — only repeating it last does
severity: medium
confidence: 0.85
created: 2026-08-10
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/llm.py
  - path: plugin/daimon_briefing/serializer.py
  - pattern: "--system-prompt"
  - pattern: "_CLAUDE_PRESET"
evidence:
  - commit: dd7b748
  - pr: 665
  - note: issue #664, 15-sample three-arm study
expires:
  condition: "a re-run of the three-arm study shows the system-role arm parsing"
  review_after: 2027-02-10
status: active
---

The claude-cli preset folds the system message into the head of one stdin blob
(`_flatten_messages`) and passes no `--system-prompt`. That looks like the bug,
and "just pass it as a real system prompt" is the obvious fix. It was tried and
it does not work.

Measured on one real chunk, 5 samples per arm, only the contract's placement
varying: head of the prompt 0/5 parsed, genuine system role via
`--system-prompt` 0/5, contract restated AFTER the transcript 5/5. Counting an
earlier single run each way: 0 of 6 as a system role, 6 of 6 restated last.
Failures were not refusals — 1,085-4,471 tokens of status lines in the
transcript's own voice, continuing the conversation.

Position is the operative variable, not role. Do not re-plumb the preset to add
`--system-prompt` expecting a fix, and do not shrink `_chunk_tail_contract()`
to a one-line reminder: `_call_and_parse`'s terse retry note already sits in
that position and lost 3 of 3 in the field.
