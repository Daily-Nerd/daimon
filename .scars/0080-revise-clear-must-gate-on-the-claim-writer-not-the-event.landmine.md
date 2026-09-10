---
id: 80
type: landmine
title: "Clearing done_* fields on a `revised` row unconditionally erases a settled HUMAN completion reachable through needs-info; gate the clear on done_pending (the AGENT-claim writer), never on the `revised` event alone"
severity: high
confidence: 0.9
created: 2026-09-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - pattern: "event == .revised."
evidence:
  - note: "#978 review round 2 (B1). A first fix (round 1, F1) landed a
expires:
  condition: "done_pending stops being the sole write site for an agent completion claim (e.g. a second claim-shaped field is added elsewhere) without updating this gate"
  review_after: 2027-03-01
status: active
---

The `revised` branch in `requests.fold` (requests.py) is the one place a sender
opens a settled record back up. Any field it clears there must be gated on
WHO WROTE the field's current value, never on the event name in isolation — a
`revised` row does not know, and must not assume, that whatever sits in
`done_*` right now came from an agent claim it is entitled to erase.

`done_pending` is the correct gate specifically because it has exactly one
writer: the non-human `done` landing on a `work` record no person has
accepted (requests.py's own #978 branch). It is never True for a human
`done`, an `info`-kind completion, or a completion on an already-accepted
record — all three of which the earlier code path already excludes from
`done_pending` in the first place. So `if current["done_pending"]:` is not an
extra check bolted on for one bug; it is the field that already answers "is
there an agent claim here" everywhere else in this same fold. A future editor
adding a new way to reach `done_*` on a record must either write through
`done_pending` too, or add its own gate here — clearing on the raw event name
is the deadend this scar exists to name.
