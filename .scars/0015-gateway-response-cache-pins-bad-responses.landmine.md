---
id: 15
type: landmine
title: Gateway exact-match response cache pins transient bad LLM responses; identical-body retries replay the garbage
severity: high
confidence: 0.9
created: 2026-06-12
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/llm.py
  - path: research/experiments/track-a/rerun.py
  - pattern: "DAIMON_LLM_NO_CACHE"
evidence:
  - note: H1 attempts 5-6 (2026-06-12): chunk 2 returned empty after 344s once; every later identical request replayed the cached empty in <1s, including the pre-cache-buster retry
  - note: "lab commit 26d6253 (pre-public-history archive)"
  - note: "firing-review 2026-09-24: re-verified the in-repo mitigations are still present — config.py:1141 DAIMON_LLM_NO_CACHE flag, and the serializer's attempt-numbered + nonce'd cache-buster retry (serializer.py:~1838-1865) — so the scar's stated workaround still holds in code. review_after bumped; the numeric gateway timing itself (cache keyed on exact request body) was not re-probed live against the real gateway, only the mitigation code was checked."
expires:
  condition: "gateway response caching disabled for the daimon key, or all daimon calls send no-cache"
  review_after: 2027-03-24
status: active
---

A LiteLLM proxy with response caching enabled caches responses keyed on the
exact request body.
A transiently-bad completion (empty content from kimi-k2.6) gets cached like
any other and is then replayed instantly for every identical request — a
one-roll failure becomes permanent for that exact prompt. Byte-identical
retries are a no-op; this defeated the serializer's first parse-retry design.
Mitigations now in tree: retries append an attempt-numbered marker
(cache-buster, serializer.py), and DAIMON_LLM_NO_CACHE=1 sends LiteLLM's
per-request bypass — REQUIRED for research runs, both to dodge poisoned
entries and because cached replays break the independent-samples assumption
of n>1 rerun studies. A 0s call duration in the serialize log is the tell
that a response came from cache, not generation.
