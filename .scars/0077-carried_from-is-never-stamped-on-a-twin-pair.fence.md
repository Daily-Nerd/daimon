---
id: 77
type: fence
title: carry.merge stamps carried_from only on the plain-carry path, never on a twin (corroborating) pair
severity: medium
confidence: 0.85
created: 2026-09-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/carry.py
  - pattern: "carried_from"
evidence:
  - note: #983 investigation: designing a read-time exclusion for already-written corroboration ledger rows (project the item's origin_session forward from the OBSERVER's own checkpoint) required a field linking the observer's stored item back to the checkpoint it was merged from. carried_from looked like that field. Reading carry.py's merge() top to bottom: the plain-carry branch (an item with no native twin) does `kept.setdefault(\"carried_from\", prev_sid)` before appending to `carried`. The twin branch (a native item that MATCHES a prev item — the exact shape a corroboration observation fires on) `continue`s out earlier, after inheriting only origin_session/origin_author onto the twin — never carried_from. So the one case this field would have been useful for (auditing which prev checkpoint a corroborating item came from) is exactly the case it is never stamped in.
expires:
  condition: "carry.merge starts stamping carried_from (or an equivalent pointer) on the twin/corroboration branch too"
  review_after: 2027-03-08
status: active
---

Do not reach for `carried_from` to reconstruct which checkpoint a corroborating
(twin-matched) item was merged from — it is unset on that path by design (see
carry.py's twin branch, ~line 470-513: only `origin_session`/`origin_author`
are inherited there, via `setdefault`). `carried_from` is stamped exclusively
on the plain-carry branch (~line 535, an item with no native match this
session), because the docstring's own reasoning is that a twin is NOT a copy
— re-stamping `carried_from` on a reworded restatement would misname the
session that actually wrote the words.

If a future change needs to answer "which checkpoint did this corroborating
item's LAST merge read as prev," it has to add a new field for that — probing
`carried_from` will silently read as absent on every twin, which looks like
"never carried" rather than "carried via the twin rail, field not tracked
here." This is why #983's change 3 (read-time exclusion of pre-fix
self-corroboration ledger rows) was deferred: there is no on-disk pointer from
a corroboration ledger row back to the origin checkpoint it was measured
against, and reconstructing one from `carried_from` does not work for exactly
this reason.
