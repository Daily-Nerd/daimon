---
id: 0
type: landmine
title: With a bucket's own events ledger unreadable, brief --team shows a teammate's copy of a value that bucket forgot; the note says the forget set is incomplete but does not withhold it
severity: high
confidence: 0.8
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "def team\("
evidence:
  - note: "#1132 PR 10a: tests/test_read_sentinel_health.py ALLOWED['own-events-os-error'] pins the leak; it is the consequence of decision 1(a) (own events NOTE for the briefing, closed only for the recall index)"
expires:
  condition: "view.team withholds on Snapshot.index_closed, or decision 1 is reversed"
  review_after: 2027-04-08
status: candidate
---

A forget scrubs this machine's own copies and leaves the tombstone in the
bucket's events ledger. A teammate's checkpoint, read through `view.team`, keeps
the value until the tombstone key is known. When the events ledger cannot be
read at all (an OS error leaves no good lines to fold) the forget set of that
bucket is empty, so `brief --team` shows the value, under a `forget set is
incomplete` note.

The sentinel census pins this as the one allowed leak of the own-events axis.
Closing `view.team` on `Snapshot.index_closed` removes it; that was not part of
the approved design, which closes only the recall index for this state. Delete
the census entry in the same change that closes the team view.
