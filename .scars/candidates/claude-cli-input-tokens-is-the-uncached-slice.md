---
id: 0
type: landmine
title: The claude CLI's usage.input_tokens is the uncached slice of the prompt, not the prompt; the prompt is the sum of three input fields
severity: high
confidence: 0.9
created: 2026-10-26
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/llm.py
evidence:
  - note: "2026-10-26, measuring for #1235: `claude -p --model haiku --output-format json --strict-mcp-config \"say ok\"` (claude 2.1.309) printed usage.input_tokens=2 beside cache_creation_input_tokens=30879 and cache_read_input_tokens=11568, output_tokens=4, total_cost_usd=0.00629368. The prompt the model read was 42449 tokens; input_tokens alone said 2."
expires:
  condition: "the claude CLI (or the Anthropic usage object it forwards) reports a single all-in input count, or llm._normalize_usage stops reading the Anthropic dialect"
  review_after: 2027-04-26
status: candidate
---

The usage object that `claude -p --output-format json` prints is the Anthropic
Messages API usage block, not an OpenAI one. There is no `prompt_tokens`,
`completion_tokens` or `total_tokens`. The input side is split by cache state:
`input_tokens` counts only the part that was neither written to nor read from
the prompt cache, and on a serialize call that part is tiny (2 tokens on the
measured call) because the transcript rides in the cached prefix. The two
cache fields carry the rest. Reading `input_tokens` as "the prompt" produces a
figure three to four orders of magnitude low, with no error and no sign.

`llm._normalize_usage` folds the three input fields into `prompt_tokens`
(Anthropic documents input as that sum) and treats an absent cache field as
zero activity, never as unknown. Anyone adding a second consumer of this
usage block (a budget, a cap, a per-chunk log line) must go through that
normalizer or repeat the same sum; a gateway in front of Anthropic can carry
both dialects in one block, and there the OpenAI `prompt_tokens` is already
the full figure (#535 keys on exactly that overlap).
