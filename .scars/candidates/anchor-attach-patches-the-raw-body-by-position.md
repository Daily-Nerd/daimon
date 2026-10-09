---
id: 0
type: landmine
title: anchor --attach must patch the raw own body by position, never rewrite the view-filtered body, or every withheld item is deleted
severity: high
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/cli/brief.py
  - pattern: "def attach_anchor"
evidence:
  - note: "#1132 PR 11b: tests/test_attach_anchor.py::test_the_rewrite_keeps_every_withheld_byte_in_place"
expires:
  condition: "the checkpoint stops holding withheld items in its own body"
  review_after: 2027-04-09
status: candidate
---

The view hands a reader a copy of the checkpoint with every quarantined,
forgotten or closed item removed. `anchor --attach` persists what it reads, so
patching that copy and calling `write_checkpoint` would delete each withheld
item from the file: a quarantine a person has not judged yet would disappear,
and a lifted quarantine could never bring the item back.

The match is `view.match(how="substring")`, which only chooses the item and
returns its `(section, key, index)`. The write is `store.attach_anchor`, which
re-reads the raw own body, patches that one position and rewrites the rest as
it was. The topic is a singleton, so its index is None. The census marks the
`attach-withheld` case as a write-carry on purpose: its rewrite keeps the
withheld bytes, and only what the verb prints is read output.
