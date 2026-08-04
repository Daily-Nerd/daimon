---
id: 40
type: landmine
title: llm.fallback_used() is a correctness gate on the chunk cache, not a display flag
severity: high
confidence: 0.9
created: 2026-08-04
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/llm.py
  - path: plugin/daimon_briefing/serializer.py
  - pattern: "fallback_used"
evidence:
  - note: serializer.py:1652 refuses every chunk-cache write once fallback_used() is true, citing the #28/#343 poisoning lesson inline.
  - note: serializer.py:1577 builds the cache key from configure.resolved_backend() — the CONFIGURED backend, which is not the backend that served the call when a fallback fired.
  - commit: 792b115
expires:
  condition: "_chunk_cache_key stamps the backend that actually SERVED the call (e.g. from llm.served_models()) instead of the configured one, making the key self-distinguishing"
  review_after: 2027-02-04
status: active
---

`llm.fallback_used()` looks cosmetic. It is set on the fallback edge in
`llm.py` and read by `render`/`ledger`/`cli` to print the `[fallback backend]`
marker and count rescues. That is the visible half.

The invisible half: `serializer._save_chunk_cache` (`serializer.py:1652`)
refuses ALL chunk-cache writes once it is true.

The reason is not that the cache key ignores the backend — it does include one.
`_chunk_cache_key` (`serializer.py:1581`) stamps
`configure.resolved_backend()`, which is the backend the operator CONFIGURED,
not the one that actually answered. When a rescue fires, the key still says
`litellm` while the bytes came from a CLI. The key therefore cannot tell
primary-produced output from rescue-produced output, and this flag is the only
thing that can. Same failure class as scar 0015, and it is also what makes the
#465 multi-producer gate (`serializer.py:1658`) meaningful.

So any NEW code path that substitutes one backend for another MUST set
`_fallback_used = True` before returning, even if it has no interest in the log
line or the counters. Forgetting it fails no test and prints nothing — it
poisons the cache for subsequent runs. When #475 added the command-primary
rescue edge, that is why both edges route through one `_rescue()` helper
instead of duplicating dispatch: the ledger-matched log literal, this flag, and
the deadline re-arm have to travel together, and two copies drift.
