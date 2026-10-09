---
id: 0
type: fence
title: "ruling ratify and revise refuse a record with a withheld field instead of printing the marker and proceeding, on purpose"
severity: high
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/ruling.py
  - path: plugin/daimon_briefing/cli/_ledger.py
evidence:
  - commit: f49ee80
  - note: "tests/test_ruling_masking.py::test_ratify_refuses_a_pending_proposal_it_cannot_show"
expires:
  condition: "a ceremony can show the withheld words to the person at a terminal without leaking them to an agent"
  review_after: 2027-04-09
status: candidate
---

Ratifying is a signature on the FULL text and binds the stored record's content key. If the ceremony masked
a field and went on, the person would sign words they could not read. So `ruling ratify`, `ruling revise`,
`ruling propose --ratify` and the `refute` verbs that act on a record ask `view.withheld_in` first and stop
with exit 2 and nothing written; a quarantine names `daimon trust show <id>` on a terminal.

The signature still binds the stored key (`displayed_key` from the raw record). Do not "fix" the refusal by
printing the marker and asking for the confirm: that is a blind signature.
