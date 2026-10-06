---
id: 0
type: fence
title: The trust ledger deleter redacts prose in place; dropping quarantine rows lifts the withhold
severity: high
confidence: 0.9
created: 2026-10-05
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/trust.py
  - path: plugin/daimon_briefing/surfaces.py
  - path: plugin/daimon_briefing/cli/lifecycle.py
  - pattern: trust\.forget_content_key
evidence:
  - note: "#1132 stage 2c-1: trust.forget_content_key dropped every row of a matching quarantine and had no caller; replaced by trust.redact_content_key"
expires:
  condition: "quarantine verdicts leave trust.jsonl, or the withhold stops depending on the rows being present"
  review_after: 2027-04-01
status: candidate
---

refutations, amendments and requests each answer a forget by dropping the
matching rows. trust.jsonl must not. A quarantine is a human verdict, and the
withhold is latched by the rows: trust.active_value_keys and every read path
consult them. Drop them and the forgotten value, or a fresh copy extracted
later, shows again. trust.forget_content_key did exactly that (removed in 2c-1).

trust.redact_content_key is the deleter for this ledger. It swaps a matching
reason or evidence entry for store._FORGOTTEN_FIELD_MARKER, redacts all prose
of the quarantine whose value_key is the forgotten key, and keeps value_key,
the verdict and every other field. It relies on the trust row's prose living
on single-segment paths (test_trust_prose_paths_are_single_segment).

A future unification of the four deleters (forget registry stage) has to keep
this ledger on redact. If a drop ever looks simpler, it is lifting a verdict.
