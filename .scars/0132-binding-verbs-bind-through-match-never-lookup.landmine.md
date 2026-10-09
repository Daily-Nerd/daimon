---
id: 132
type: landmine
title: A verb that writes an event for an id must bind through view.match, never through lookup, because any later event on a tombstoned id lifts its tombstone
severity: high
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/cli/lifecycle.py
  - path: plugin/daimon_briefing/cli/amend.py
evidence:
  - note: #1132 PR 11b: tests/test_forgotten_write_pin.py and tests/test_resolve_withheld.py::test_a_forgotten_id_is_not_a_write_target
expires:
  condition: "PR 12 namespaces the tombstone so a later event no longer lifts it"
  review_after: 2027-04-09
status: active
---

`view.forgotten_ids` keeps an id only while its latest event is a tombstone, so
the next `resolve`, `reverify` or amendment event on that id replaces the
tombstone and the forgotten value shows again. `view.lookup` answers a
tombstoned id with a `Withheld(forgotten)`, which is right for a question
(`why`, `blame`) and wrong as a write target.

`resolve`, `reverify` and `amend propose` therefore bind through
`view.match`, which drops a forgotten candidate and counts it nowhere, so a
tombstone-only id reads as no match on every channel. `forget` follows the
same rule: re-forgetting is not a need, `ledger repair` scrubs residue. Do not "simplify" a binding verb
onto `lookup` to share code with `why`. `daimon log` takes no id, so it cannot
lift a tombstone today; the general rule is PR 12's.
