---
id: 0
type: landmine
title: A new bucket ledger that recall's rebuild folds into the index must also be added to store.INDEX_CONTENT_LEDGERS, or confirming/writing it after the index exists serves stale rows until an unrelated file changes
severity: critical
confidence: 0.95
created: 2026-09-24
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/recall.py
  - pattern: "INDEX_CONTENT_LEDGERS"
evidence:
  - note: "#1109 PR 2 (Daily-Nerd/daimon), commit 8062bf3 review: trust.jsonl
      shipped a full read-path withholding pass (recall, briefing, MCP,
      daimon_ui) with every unit test passing, but recall._fingerprint's
      staleness key never listed trust.jsonl. Every test that proved
      withholding confirmed the quarantine BEFORE the index existed, so the
      very first rebuild already reflected it and gave zero signal about
      staleness — the bug shipped green."
expires:
  condition: "recall._fingerprint is refactored to derive its walked ledger
    set from a single registry that both the fold code and the fingerprint
    code read (removing the possibility of updating one without the other)"
  review_after: 2027-03-24
status: candidate
---

`recall._fingerprint()` hashes `(path, mtime_ns, size)` for a fixed set of
files to decide whether the sqlite index is stale. For files inside a
project bucket it walks `*.json` plus whatever is named in
`store.INDEX_CONTENT_LEDGERS` — NOT everything `rebuild()` reads. Adding a
ledger that `rebuild()` folds into the index (a new `_apply_*` function
reading a new `.jsonl` file) without ALSO adding that filename to
`INDEX_CONTENT_LEDGERS` produces a database that is byte-for-byte the SAME
staleness key before and after the new ledger changes — so `_ensure_fresh()`
sees no reason to rebuild, and `search()`/`suggest()`/every MCP tool built on
them keep serving the OLD row set until some unrelated file (any checkpoint
write, any events.jsonl append) happens to touch the fingerprint for an
unrelated reason.

The trap that let this ship: every test built through `trust.propose()` and
then called `recall.rebuild()`, `recall.search()`, or `recall.suggest()` for
the FIRST TIME in that test — with no existing index, the very first
`_ensure_fresh()` call does a full rebuild regardless of the fingerprint, so
the new ledger's content is correct by construction and the test proves
nothing about staleness. A test that actually exercises this bug MUST warm
or query the index once BEFORE the ledger write, then write/confirm/release
through the real function SECOND, then query again with no manual
`rebuild()` call anywhere in between — only then does a missing fingerprint
entry cause a visible failure (item still there, or still gone, when it
should have flipped).

A future editor adding a fourth (or later) ledger that `rebuild()` reads must
add its filename to `store.INDEX_CONTENT_LEDGERS` in the same commit, and
must write at least one test in the "index built first, ledger written
second, no manual rebuild" shape — the enumeration-matches-itself shape
(asserting the fingerprint walks the same set `INDEX_CONTENT_LEDGERS`
declares) proves nothing, per the sibling scar about residue tests that walk
the scrubber's own surface set.
