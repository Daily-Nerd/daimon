---
id: 32
type: landmine
title: A gateway model alias is routing config, not provenance — the wire's response.model is the only per-call truth
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/llm.py
  - path: plugin/daimon_briefing/serializer.py
evidence:
  - note: 2026-07-30 live probe: a request naming claude-haiku-4-5-via-meridian was served by unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF (response.model field; local fallback lane). The operator's gateway defines silent fallback chains ending in a local model — resilience by design, active since 2026-06-25, so any alias-labeled call may have been served by a different model with no error.
  - note: benchmark/results/longmemeval-s-baseline.json stamps model: claude-haiku-4-5-via-meridian for 192 serialize calls (2026-07-12); the fallback chain was live that day, so per-call model purity is unprovable post-hoc. A no-fallback bench lane was added gateway-side 2026-07-17 for exactly this reason.
expires:
  condition: "provenance stamping records the served model from the response (response.model) alongside the requested alias, and surfaces a mismatch"
  review_after: 2026-10-30
status: active
---

`_stamp_llm_provenance` and the bench config stamp record the model NAME THE
CALLER REQUESTED. Behind an OpenAI-compatible gateway that name is an alias,
and gateways implement silent fallback chains: the call succeeds, the model
differs, no error is raised. On 2026-07-30 the configured "haiku" alias was
observed serving a local Qwen3-30B — every checkpoint stamped that day names a
model that never ran. The response body carries the truth (`response.model`);
daimon currently discards it. Any future editor touching provenance stamping,
backend selection, or benchmark config stamps must treat the requested alias
as routing config only: capture the served model per call from the response,
stamp both, and flag disagreement. Measurements attributed to an alias without
a served-model receipt are unverifiable and must say so.
