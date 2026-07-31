---
id: 35
type: landmine
title: A PROMPT_VERSION bump does NOT rotate the #48 chunk cache — its own historical comment says it does, but the cache key is EXTRACTION_VERSION
severity: medium
confidence: 0.9
created: 2026-07-29
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/serializer.py
evidence:
  - commit: #416
  - note: #416 added extraction rule 21 (prefer a quote span that keeps a temporal span). The pre-#367 D-015/D-016 comment blocks claim the PROMPT_VERSION bump 'rotates the #48 chunk-cache key' — but since #367 the cache key (_chunk_cache_key) hashes EXTRACTION_VERSION, not PROMPT_VERSION. Bumping only PROMPT_VERSION would have served stale date-dropping cached extractions for up to chunk_cache_days (default 3), silently poisoning the citable-date re-measurement the change exists to make honest.
expires:
  condition: "the outdated 'rotates the #48 chunk-cache key' phrasing is removed from every pre-#367 comment block above PROMPT_VERSION, or the cache key is re-keyed on PROMPT_VERSION"
  review_after: 2026-12-01
status: active
---

When you add an extraction-behavior change (a new numbered rule that changes
what a chunk pass extracts — quote-span selection, a new emitted field, etc.),
bumping `PROMPT_VERSION` alone is NOT enough. `_chunk_cache_key` (serializer.py,
#48/#367) is keyed on `EXTRACTION_VERSION`, the scene flag, backend, model, and
temperature — deliberately NOT on `PROMPT_VERSION` or the prompt text, so
wording-only edits keep the cache warm. This is pinned by
`test_chunk_cache_key_survives_prompt_version_bump` and
`test_chunk_cache_key_rotates_on_extraction_version_bump`.

The trap: the older comment blocks (D-015 -> D-016, D-014 -> D-015) still say
the PROMPT_VERSION bump "rotates the #48 chunk-cache key." That was true before
#367 introduced the separate `EXTRACTION_VERSION` lever; it is false now. A
future author who trusts that comment and bumps only PROMPT_VERSION will get a
correct-looking checkpoint format version while the chunk cache keeps returning
pre-change extractions for up to `config.chunk_cache_days` (default 3) — the
exact window the privacy design relies on. For #416 this would have meant new
sessions serving cached quotes that dropped the date, defeating the point.

Rule of thumb: if the change alters WHAT gets extracted, bump BOTH
`PROMPT_VERSION` (checkpoint format / comparability) and `EXTRACTION_VERSION`
(cache rotation). If it only reworded the prompt without changing extraction,
bump neither the cache lever nor, arguably, the format version.
