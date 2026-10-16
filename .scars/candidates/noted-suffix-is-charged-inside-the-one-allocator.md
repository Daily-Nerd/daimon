---
id: 0
type: fence
title: The noted suffix is charged to the one byte allocator, so making it cheaper means re-measuring displacement, not adding a second budget
severity: medium
confidence: 0.8
created: 2026-10-16
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
evidence:
  - commit: 9dac0ca
expires:
  condition: "select stops charging each candidate its rendered line, or the noted line moves out of the item line"
  review_after: 2027-04-16
status: candidate
---

Every carried item that names a PR, branch, file or dependency ends with
` (noted YYYY-MM-DD HH:MMZ, re-check before acting)`, 50 bytes
(`briefing.noted_suffix`). `select` charges each candidate its rendered line
and drops whole candidates to stay under the effective budget, so the suffix
cannot push a brief past its ceiling. What it costs is displacement: a
lower-ranked item makes room.

Do not add a separate cap or budget for the line, and do not shorten it to
save room without measuring. A second budget would let the suffix and the
items disagree about the ceiling, which is the failure the single allocator
exists to prevent. tests/test_briefing_select.py pins the 50-byte charge and
the displacement; a change that moves the number has to move those tests on
purpose, with a before and after size of a real checkpoint.
