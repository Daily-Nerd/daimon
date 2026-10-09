---
id: 130
type: landmine
title: A test that plants a garbage or torn line and THEN calls a writer on that ledger gets the write refused or skipped
severity: low
confidence: 0.9
created: 2026-10-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - pattern: "events\.jsonl.*write_bytes"
evidence:
  - note: #1132 PR 10b: four existing tests had to write their rows before planting the junk
expires:
  condition: "the write exits stop judging the ledger"
  review_after: 2027-04-08
status: active
---

Since the write exits judge their ledger, a ledger holding a garbage line (or an
undecodable byte) is unproven and `append_event`, the module appenders and
`write_checkpoint` refuse or skip it. A torn tail alone is DEGRADED and still
writes. A fixture that wants the reader to cope with junk writes its rows through
the real writer FIRST and plants the junk with plain bytes afterwards. The same
holds for `forget`, which refuses an unproven events ledger before it touches
anything; a test of the redaction itself calls `store.scrub_event_fields`.
