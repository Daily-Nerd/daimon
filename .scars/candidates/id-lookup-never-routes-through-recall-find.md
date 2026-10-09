---
id: 0
type: fence
title: An exact-id lookup reads the recall index through index_locate (read-only, never refreshed), not through recall.find, which stats the whole store and rebuilds on a withheld row
severity: high
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - pattern: "def lookup_many\("
  - path: plugin/daimon_briefing/index_locate.py
evidence:
  - note: "#1132 PR 11a: tests/test_view_lookup_many.py::test_an_id_nothing_holds_costs_no_session_parse_and_no_rebuild and tests/test_why_cost.py patch recall.rebuild, _rebuild_forced and _ensure_fresh to raise and run a lookup"
expires:
  condition: "recall.find stops calling _ensure_fresh and _rebuild_forced, or the locator is replaced by a bucket-local id table"
  review_after: 2027-04-09
status: candidate
---

`view.lookup_many` locates an id with `index_locate.locate`: the index is
opened `mode=ro`, the schema version is checked, one indexed query runs per
chunk of 500 ids, and the file is never refreshed or written. The value of a
located item comes from the named session file through the view. A row whose
file is not local (a teammate's mirror) or has no project stamp is answered
from the row and classified at read time; a torn file or one stamped for
another project gives `Absent` with an `unreadable` or `foreign-stamp` note.
An id serialized since the last index build is found through the pointer
window only.

`recall.find` stats every checkpoint, ledger and team file (`_ensure_fresh`)
and rebuilds on a withheld row (`_rebuild_forced`), so an id lookup must not
call it and the locator must not gain a refresh. `recall.find` stays for
`pending._loop_text`, which has its own reasons.
