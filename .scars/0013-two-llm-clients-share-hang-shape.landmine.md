---
id: 13
type: landmine
title: Two independent LLM clients (lib/llm.py + evaluate.py) share the stall-hang shape — harden both
severity: high
confidence: 0.9
created: 2026-06-30
authors: ["claude-code", "kibukx"]
anchors:
  - path: research/experiments/lib/llm.py
  - path: research/memory-backend/benchmark/evaluate.py
evidence:
  - commit: d8d0fb9  # squash-merge of PR #69
  - commit: 162e49c  # squash-merge of PR #71
  - pr: 69
  - pr: 71
expires:
  condition: "the two clients are unified into one shared module"
  review_after: 2027-06-30
status: active
---

The benchmark harness has TWO separate OpenAI-compatible chat clients, and they
do NOT share code:

- `research/experiments/lib/llm.py::chat` — used by the Track-A grounding path
  (verify_live.py, runner.py, rerun.py, probe_d007.py).
- `research/memory-backend/benchmark/evaluate.py::LLMClient.chat_completion` —
  used by the memory scale-test (run_scale_benchmark.py).

Both had the identical "ornith" failure mode: a reasoning model whose context
window can't fit the transcript stalls, the client's retry loop catches the
timeout and retries N×300s, and the run hangs for ~15 min before failing. Fixing
ONE client (lib/llm.py, PR #69) does nothing for the other — the memory runner
kept hanging until evaluate.py was fixed separately (PR #71).

If you change timeout/retry behavior, the pre-flight context check, or the
`LITELLM_CONTEXT_WINDOW` env contract in one client, mirror it in the other or
the two paths drift. Do not assume "I fixed the LLM client" — ask WHICH client
the failing entrypoint actually imports first.
