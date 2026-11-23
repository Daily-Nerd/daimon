---
id: 0
type: fence
title: A recipient verb refuses an id nobody sent, but only an agent must be addressed; a person decides from any directory
severity: medium
confidence: 0.85
created: 2026-11-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/requests.py
  - pattern: "def _answering"
evidence:
  - commit: 36932a3
  - note: "done, accept, reject, needs-info and suppress wrote an orphan row for any id of the right shape. The first fix required the ask to be addressed to the project for every channel and broke the #895 tests, where a person answers from a third bucket (the join already pairs that row with its origin)."
expires:
  condition: "a person's verdict from a directory other than the recipient is removed from the join, or every recipient verb routes through the recipient slug"
  review_after: 2027-05-23
status: candidate
---

`requests._answering` is the one write boundary of the recipient verbs. It
refuses an id that no bucket ever opened, so a typo or a request meant for
another project cannot land as an orphan row.

It is deliberately asymmetric. An agent channel speaks for its own project, so
the ask must be addressed to it (the recipient join). A human channel may
decide from any directory (#895), so an ask that exists anywhere passes and
the row lands in whatever bucket the person stood in. Tightening the person
case to "addressed to this project" looks like the safer rule and fails the
third-bucket tests, because the read-time join is what pairs that row with its
origin.

A request offered but not yet accepted is addressed to the project, so an
agent `done` on it stays a held claim (#1333). Do not read that as the guard
missing a case.
