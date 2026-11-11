---
id: 155
type: landmine
title: A request kind that skips the receiver's coverage predicate leaves its ruling inert and its agent unchecked; keep one kind-agnostic predicate for the write boundary and the fold
severity: high
confidence: 0.85
created: 2026-11-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - path: plugin/tests/test_request_receiver_consent.py
evidence:
  - note: plugin/tests/test_request_receiver_consent.py::test_an_agent_accepted_row_older_than_the_rule_keeps_landing protects historical pre-anchor agent accepts.
  - note: Receiver consent for info asks. The accept write boundary and the fold each carried an unconditional info branch, and both coverage predicates compared the ruling kind to the literal work, so a verb=accept kind=info ruling validated, was ratified, and changed nothing. An agent's done on an info ask also skipped the accept entirely because the fold held back only work.
expires:
  condition: "request kinds stop being a closed pair, or coverage moves behind a single resolver both boundaries call"
  review_after: 2027-05-01
status: active
---

Answering an ask exposes what the receiving project knows, so every agent verb that
lands on it needs the receiving project's own ratified consent, whatever the ask's
`kind`. Three places decide it and they must agree: `accept()` at the write boundary,
the fold's `_HUMAN_ONLY` exception for an agent `accepted`, and the fold's hold-back
of an agent `done` (`done_pending`).

The trap is special-casing one kind in any of them. A `kind == "info"` shortcut in
the accept path, or a coverage predicate comparing the ruling's kind to the literal
`"work"`, makes the other kind's ruling validate but never fire, and lets a verb that
was not special-cased (here `done`) walk around the check. `_covered_by_policy` and
`_resolve_covering_ruling` take the record's own `kind` and compare against it.

Rows written before the rule keep their state: the fold anchors the old behaviour by
row `order` (`RECEIVER_CONSENT_FROM_NS`), so refolding a ledger never rewrites a past
verdict. Do not move that constant backwards, and do not drop the anchor to "simplify"
the fold; the pin is the red test that refolds a pre-anchor agent accept.
