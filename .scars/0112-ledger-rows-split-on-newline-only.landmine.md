---
id: 112
type: landmine
title: Split ledger text on "\n" only through jsonl.split_rows; str.splitlines() tears rows holding U+2028/U+2029/U+0085
severity: high
confidence: 0.9
created: 2026-10-03
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/jsonl.py
  - pattern: "read_text\(encoding=.utf-8.\)\.splitlines"
evidence:
  - note: #1138; every ledger writes json.dumps(ensure_ascii=False), so the three characters sit raw in a row
expires:
  condition: "readers and rewriters all go through jsonl.split_rows and no splitlines() on ledger text remains"
  review_after: 2027-04-01
status: active
---

`str.splitlines()` also breaks on U+2028, U+2029, U+0085 (and \x0b, \x0c,
\x1c-\x1e). Ledger rows carry those characters raw, so a splitlines() rewriter
tears the row, sees two "unparseable" fragments, and drops them: an unrelated
forget deleted the row for good. The same split made scrub_event_fields copy
fragments through and leave a forgotten value in place, and let the privacy
audit report such a file clean. A past scrub also persisted the split as a
raw "\n" inside a JSON string, which jsonl.split_rows rejoins with U+2028.

Rewriters, scans and readers must use jsonl.read / split_rows / read_rows /
rewrite (rewrite keeps unparseable lines verbatim and round-trips undecodable
bytes; read judges each line on its own and returns the rows around a bad
one). Every ledger reader in daimon_briefing is on jsonl.read now. Still on
splitlines() by design: store._tombstone_keys (byte-capped read), the raw line
count in cli._stats_events, and daimon_ui/reader.py (no daimon imports).

Coupling found while doing it: store.scrub_event_fields must keep writing via
store._atomic_write (jsonl.rewrite(write=...)) and hold the admitted row in
its own frame, or test_write_audit_guard stops seeing a governed rewrite.
