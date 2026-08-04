---
id: 0
type: landmine
title: llm._fallback_used is a correctness gate on the chunk cache, not a display flag
severity: high
confidence: 0.9
created: 2026-08-04
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/llm.py
  - pattern: "_fallback_used"
evidence:
  - note: "#475 design recon, 2026-08-04: adding a second dispatch edge (command primary -> rescue command) required setting _fallback_used on the new edge; a rescue that left it clear would have cached the rescue CLI's output under the primary's key."
  - commit: e66caf6
expires:
  condition: "_save_chunk_cache stops keying on llm.fallback_used() — e.g. the cache key carries the producing backend identity directly"
  review_after: 2027-02-04
status: candidate
---

`llm._fallback_used` reads like a cosmetic flag. It is set in `llm.py` on the
fallback edge and read by `render`/`ledger`/`cli` to print the
`[fallback backend]` marker and count rescues. That is the visible half.

The invisible half: `serializer._save_chunk_cache` gates the chunk-extraction
cache on `llm.fallback_used()`. Chunk cache entries are keyed by transcript
content, NOT by which backend produced them, so the flag is the only thing
stopping a weaker backend's output from being written under a key that a later
run will read back as if the primary produced it. It is also what makes the
#465 multi-producer gate work at all.

So any NEW code path that substitutes one backend for another MUST set
`_fallback_used = True` before returning, even if that path has no interest in
the log line or the counters. Forgetting it does not fail a test and does not
show up in output — it silently poisons the cache for subsequent runs, which is
the same failure class scar 0015 covers for gateway response caching.

When #475 added the command-primary rescue edge, this is why the rescue was
routed through a single shared `_rescue()` helper rather than duplicating the
dispatch inline: the log literal (a ledger contract), the flag, and the #341
deadline re-arm all have to travel together, and two copies drift.
