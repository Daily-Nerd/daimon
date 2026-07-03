---
id: 17
type: landmine
title: Gateway kills LLM requests at ~815s; dense K=3 merges on kimi exceed it and can never complete
severity: high
confidence: 0.85
created: 2026-06-12
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/serializer.py
  - path: plugin/daimon_briefing/config.py
  - pattern: "DAIMON_MERGE_GROUP_SIZE"
evidence:
  - note: H1 attempt 7 (2026-06-12): four consecutive merge calls killed at 815s/815s/814s/816s (HTTP 502 or empty 200). Client DAIMON_TIMEOUT=1800 irrelevant — the cut is server-side.
  - note: H1 attempt 8: K=2 merges completed in 149-484s; 12k-28k completion tokens per merge. K=3 on dense checkpoints needs >815s of generation.
expires:
  condition: "gateway request timeout raised past 1800s, or merges moved to a faster non-reasoning model (FR #16)"
  review_after: 2026-09-12
status: active
---

Self-hosted gateway stacks (LiteLLM or its upstream; observed on ours) can
terminate requests at ~815 seconds regardless of the client's socket timeout. kimi-k2.6 is a
reasoning model: merging 3 dense chunk-checkpoints generates >815s of
reasoning+output tokens, so every such call dies at the ceiling and retries
are a lottery with no winning tickets. Raising DAIMON_TIMEOUT does nothing —
the 502 (sometimes an empty 200) is server-side. For sessions with dense
checkpoints (H1: 156 items), set DAIMON_MERGE_GROUP_SIZE=2 on this gateway;
2-input merges peaked at 484s. Lighter sessions (S1) fit K=3. Permanent
fixes: raise the gateway request timeout, or route merges to a fast
non-reasoning model. Symptom signature in logs: repeated 502s at near-
identical call ages (~13.5 min).
