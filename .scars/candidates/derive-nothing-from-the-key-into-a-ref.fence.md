---
id: 0
type: fence
title: A forget's key ref is a random token and is never derived from the content key
severity: high
confidence: 0.85
created: 2026-10-10
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def new_key_ref"
evidence:
  - commit: d48a0c1
expires:
  condition: "the key row moves out of events.jsonl into a store that never prints a ref"
  review_after: 2027-04-10
status: candidate
---

The key row of a forget sits on a `forgotten-key:` ref, and the ref is
`secrets.token_hex(8)` on purpose. A ref derived from the content key (a
digest, a prefix, the key itself) is a fixed function of the value, so anyone
holding a candidate text can compute it and ask "was this forgotten?" of every
surface that prints a ref. A digest of a 64-bit key is exactly as strong an
oracle as the key. The viewer activity feed prints `item_ref` raw, which is why
`view.events` drops key rows, and a literal `forgotten-key:<key>` ref would put
the content key into `/api/activity` (scar 0119).

Nothing needs the ref to be derivable: `store.standing_tombstones` maps a key
to its refs from the status, and idempotence asks that map. Do not "make it
stable" or "make it deterministic". Add every key ref the sentinel world
writes to its scan tokens instead.
