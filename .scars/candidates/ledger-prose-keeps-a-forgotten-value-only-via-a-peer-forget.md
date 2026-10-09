---
id: 0
type: landmine
title: "A ledger prose column holds a whole forgotten value only when another project forgot it after this one wrote it, so a census must plant in that order"
severity: medium
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/tests/_sentinel_world.py
evidence:
  - commit: 65f2baf
  - note: "#1132 PR 11c: the first plant after the in-bucket forget left no trace in refutations.jsonl or requests.jsonl"
expires:
  condition: "writers stop re-scrubbing a forgotten value on append, or the deleters stop dropping whole records"
  review_after: 2027-04-09
status: candidate
---

Ledger writers re-scrub a forgotten value as they append (`jsonl.reaching` heals the whole ledger),
and the refutation, request and amendment deleters drop whole records. A row written AFTER a forget in
the same bucket therefore never keeps the forgotten text: the first attempt at a forgotten-kind plant
left zero bytes on disk and the census would have proven nothing.

The only way a whole forgotten value sits in ledger prose is a forget made in ANOTHER project after this
one wrote it (the machine-wide key set grows, no heal runs here). `_write_forgotten_prose` writes the rows
first and then appends the tombstone to the peer's `events.jsonl`. Keep that order, and any later write
verb that appends to those ledgers in a test will heal the plant away, so drive such verbs on a copy.
