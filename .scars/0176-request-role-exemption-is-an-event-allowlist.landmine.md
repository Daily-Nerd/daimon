---
id: 176
type: landmine
title: The pre-rule exemption from the request role check must stay a frozen allowlist of events, never "any row dated before the cutoff"
severity: high
confidence: 0.85
created: 2026-11-15
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config
promoted_from: candidates/request-role-exemption-is-an-event-allowlist.md
anchors:
  - path: plugin/daimon_briefing/requests.py
evidence:
  - note: issue 1348, narrowing the ROLE_RULE_FROM_NS exemption to _PRE_ROLE_RULE_EVENTS
expires:
  condition: "request events carry a schema version the fold can read, so a row's age no longer decides which rule applies"
  review_after: 2027-05-15
status: active
---

`ROLE_RULE_FROM_NS` lets a row ordered before the cutoff skip the sender/recipient role check,
so old ledgers keep their meaning. Applied to every event it also let a hand-dated row of an
event that did not exist at the cutoff (`withdrawn`, and any later one such as `severity_set`)
land from the wrong bucket: the row's `order` is a field anyone can write.

The exemption is `event in _PRE_ROLE_RULE_EVENTS and _written_before(row, ROLE_RULE_FROM_NS)`.
The set is frozen at the events that existed on 2026-11-15 and is never added to. A new
request event is outside it by default, and tests/test_request_roles.py pins that. Do not
widen it to make an old-looking row land, and do not replace it with a check on the row's age
alone.
