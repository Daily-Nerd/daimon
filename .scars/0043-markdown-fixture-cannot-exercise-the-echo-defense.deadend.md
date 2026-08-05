---
id: 43
type: deadend
title: A markdown or uuid-less transcript fixture silently measures the echo defense as ABSENT — it reports a false verbatim, it does not fail
severity: medium
confidence: 0.9
created: 2026-08-05
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/tests/test_quote_verification.py
  - path: plugin/daimon_briefing/transcript.py
evidence:
  - note: 2026-08-05, #577 extraction probe. A .md fixture holding `daimon refute guard` output serialized to items tagged `verbatim`, the strongest trust class, because markdown has no tool_use/tool_result blocks for `_daimon_tool_use_ids` to pair. Rebuilt as .jsonl with a correct pair, still 0 rows flagged, because `transcript.py` only surfaces a tool_result row when it also carries a row-level `uuid`. Adding uuids gave `daimon_output_ids: {'u0009'}` on the third attempt.
expires:
  condition: "from_file tags daimon output on any transcript shape, or refuses a fixture whose daimon invocations cannot be paired"
  review_after: 2027-02-05
status: active
---

Measuring anything about the #440/#441/#512 echo defenses needs a fixture that
can carry provenance. Two shapes cannot, and neither one errors:

- **markdown** — no tool blocks at all, so daimon's own output reads as ordinary
  prose and quote verification grades it `verbatim`.
- **JSONL without a row `uuid`** — the blocks parse, but the tool-message branch
  is gated on the row uuid, so the row is dropped before `daimon_output` is set.

Both fail toward "the defense is not there", which is the same result a real
regression produces. A probe concluding that daimon echoes its own output at
`verbatim` has probably measured its fixture.

Build the fixture as `.jsonl`: a `tool_use` whose `input.command` invokes
daimon, a `tool_result` carrying `tool_use_id`, and a `uuid` on every row. Then
assert `serializer.daimon_output_ids(messages)` is non-empty BEFORE spending a
serialize on it. That check is free and it is the only thing separating a real
measurement from a fixture artifact.
