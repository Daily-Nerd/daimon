---
id: 0
type: fence
title: forget by exact id binds a forgotten id on a person's channel and reads as no match to an agent, on purpose
severity: medium
confidence: 0.75
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/lifecycle.py
  - pattern: "reason == \"forgotten\" and not human"
evidence:
  - note: "#1132 PR 11b: tests/test_forget_candidates.py::test_a_forgotten_exact_id_is_no_match_to_an_agent_and_a_target_to_a_person"
expires:
  condition: "forget stops leaving plaintext behind a tombstoned id"
  review_after: 2027-04-09
status: candidate
---

A tombstone can name an id while the value is still on disk (a sibling id, or a
value redacted differently at capture), so a person must still be able to run
`daimon forget <id>` and finish the deletion. That makes forgotten the odd row
in the verb: every other binding verb treats a forgotten id as absent.

The gate is the channel. A person gets the withheld marker in `--dry-run` and
the normal receipt. An agent gets `no item matches`, so an exact id cannot tell
it whether the id was stored and forgotten. A quarantined id follows the same
rule (a person binds it, an agent is refused). A closed trust ledger is the
reverse: `forget` still works on every channel, because the deletion promise
outranks the outage and the id names no value. Do not merge the three cases
into one "withheld" branch.
