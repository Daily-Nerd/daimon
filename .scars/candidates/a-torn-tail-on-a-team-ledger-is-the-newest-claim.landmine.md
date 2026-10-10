---
id: 0
type: landmine
title: A torn last line of a teammate's published ledger is their newest claim, so the author is skipped, not read around
severity: high
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def foreign_team"
evidence:
  - commit: 5a1d7a3
  - note: tests/test_foreign_quarantines.py::test_a_torn_tail_is_unproven_not_degraded_h7
expires:
  condition: "published rows are appended atomically and a torn line can only be an old one"
  review_after: 2027-04-09
status: candidate
---

An append-only ledger tears at its tail when a pull or a crash cuts the last
write. For a local ledger that is a line to fold around. For a teammate's
`quarantines.jsonl` the tail is the author's newest claim, and for quarantines
the newest claim is the one that matters most (a new `active`). Reading around
it would admit the author's checkpoints while the claim that should withhold
part of them is missing.

So a torn tail (jsonl health DEGRADED) makes the author unproven, like an
unreadable or over-cap file: their checkpoints are not admitted and the note is
`author-skipped`. The good rows before the tear still count, because the pair
set only grows. The cost is that one bad line skips an author everywhere until
the file is clean; that is stated in docs/team.md. Do not downgrade DEGRADED to
a note for a team ledger.
