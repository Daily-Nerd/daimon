---
id: 0
type: landmine
title: events.jsonl carries plaintext (item_text, note, status), so forget judges its reach even though writes treat a torn tail as proven
severity: medium
confidence: 0.85
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/ledger_repair.py
  - pattern: "_event_holds"
evidence:
  - note: "#1132 PR 10b: tests/test_forget_reached.py::test_a_torn_events_row_is_not_reached_and_forget_exits_4"
expires:
  condition: "events rows stop carrying item_text, note or free-form status"
  review_after: 2027-04-08
status: candidate
---

The ledger is described as "hashes only" in places, and it is not: `resolve` and
`reopen` write the item's full text into `item_text`, and `note` and `status` are
free-form (#599). `store.scrub_event_fields` redacts them in place, and
`jsonl.rewrite` writes a torn line back verbatim, so a torn events row can still
hold the value. Writes and the forgotten set rightly treat a torn tail as proven
(DEGRADED), but forget's REACH is a separate judgement: it lists events.jsonl as
unreached and exits 4 with the repair line. Do not fold the two judgements.
