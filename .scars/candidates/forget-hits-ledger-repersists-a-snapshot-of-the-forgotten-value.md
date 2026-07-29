---
id: 0
type: fence
title: The forget-hits ledger re-persists a short snapshot of the value forget removed — deliberate, bounded, and in tension with #321
severity: medium
confidence: 0.85
created: 2026-07-29
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "record_forget_hits"
evidence:
  - pr: 405
  - note: "#404 asks for a short claim_text snapshot of what was suppressed; #321/forget's whole premise is that a forgotten value's content leaves disk. record_forget_hits writes {ts, key, claim} to forget-hits.jsonl, so the claim field re-introduces to disk a (redacted, <=60 char) prefix of the very value the tombstone removed."
expires:
  condition: "a privacy pass drops the claim field (key + ts only) OR the ledger is proven to hold no content forget was meant to erase"
  review_after: 2027-07-29
status: candidate
---

`daimon forget` (#321) exists so a value's content leaves disk and the audit
trail entirely — the tombstone stores a HASH, never the text. #404 then asks
the forget-hits ledger to "retain a short claim_text snapshot of what was
suppressed" so a human can see WHAT the tombstone caught, not just a count.
Those two goals pull opposite ways: `record_forget_hits` writes a `claim`
field, which puts a prefix of the forgotten value back on disk (in
`forget-hits.jsonl`), exactly the content forget was meant to erase.

The tension is bounded on purpose, not resolved: the snapshot is run through
`redact.redact_text` and truncated to `_FORGET_CLAIM_MAX` (60 chars), and
`daimon status` surfaces ONLY the count + last_hit_at — never the claim. So the
claim lives locally in the ledger file and never reaches the status line, a
briefing, recall, or a team mirror.

What a future editor must NOT do: assume forget-hits.jsonl is content-free
(it is not — it holds a redacted claim prefix), and do not widen the snapshot,
surface `recent` claims in status/brief, or mirror this ledger to the team dir
without re-checking this fence. If a privacy pass decides the count is enough,
drop the `claim` field (keep `key` + `ts`) and this fence expires.
