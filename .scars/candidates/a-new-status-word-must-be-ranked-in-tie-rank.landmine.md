---
id: 0
type: landmine
title: A new lifecycle status word must be ranked in _tie_rank, is_resolved and the status class tuple
severity: medium
confidence: 0.8
created: 2026-10-10
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def _tie_rank"
evidence:
  - commit: ed7fd95
expires:
  condition: "the status vocabulary is an enum with one table of ranks"
  review_after: 2027-04-10
status: candidate
---

Three places read the first word of a status: `is_resolved` (does it close the
item), `_tie_rank` (who wins a same-second tie) and `_STATUS_CLASS_PREFIXES`
(which word survives a redaction). A lift that is a new word but not ranked
falls to rank 1 beside `forgotten:`, and the tie is then decided by
canonical-JSON order, so a forget and an unforget written in the same second
leave the value withheld half the time. A plain
`unforgotten` status would have had exactly that flaw.

`reopened:unforget` starts with `reopen`, behind `store.is_lift_status`, and
`_tie_rank` names it. Add a test that writes both orders of a same-second tie
for any new word.
