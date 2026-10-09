---
id: 128
type: fence
title: "ruling ratify and revise refuse a record with a withheld field instead of printing the marker and proceeding, on purpose"
severity: high
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/cli/ruling.py
  - path: plugin/daimon_briefing/cli/_ledger.py
  - path: plugin/daimon_briefing/cli/refute.py
evidence:
  - commit: e5aea9e
  - note: tests/test_ruling_masking.py::test_ratify_refuses_a_pending_proposal_it_cannot_show
expires:
  condition: "a ceremony can show the withheld words to the person at a terminal without leaking them to an agent"
  review_after: 2027-04-09
status: active
---

Ratifying is a signature on the FULL text and binds the stored record's content key. If the ceremony masked
a field and went on, the person would sign words they could not read. So `ruling ratify`, `ruling revise`,
`ruling propose --ratify` and the `refute` verbs that act on a record ask `view.withheld_in` first and stop
with exit 2 and nothing written, on EVERY channel: a quarantine is a human latch and the record stays frozen until `daimon trust show <id>` on a terminal and `daimon trust release <id>`, which the refusal names. Do not narrow it to the tty.

The signature still binds the stored key (`displayed_key` from the raw record). Do not "fix" the refusal by
printing the marker and asking for the confirm: that is a blind signature.
