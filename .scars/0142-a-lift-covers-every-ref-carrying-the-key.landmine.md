---
id: 142
type: landmine
title: A lift must be written on every standing ref that carries the key, not only the key ref
severity: high
confidence: 0.85
created: 2026-10-10
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/forget.py
  - pattern: "def lift"
evidence:
  - commit: 93a3a43
expires:
  condition: "the id rows stop naming the key in their status"
  review_after: 2027-04-10
status: active
---

A forget stands on three kinds of ref: the item id, every sibling id and a key
ref, and each id row carries the content key in its own `forgotten:<key>`
status. `_tombstoned_keys` takes the key from the latest event of EVERY ref, so
lifting only the key ref (or only the id the person named) leaves the key
standing through the others, and the verb would say it lifted a value that is
still withheld. The same value can also stand on several ids: a text-query
forget of a second record id appends another tombstone.

`forget.lift` reads `store.standing_tombstones` and appends one
`reopened:unforget` row per standing ref, in one write. The verdict then proves
the lift against `view.machine_sets()` and exits 4 when something else still
withholds the value.
