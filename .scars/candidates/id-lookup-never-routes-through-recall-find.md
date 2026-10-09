---
id: 0
type: fence
title: An exact-id lookup reads the recall index through index_locate (read-only, never refreshed), never through recall.find, because find stats the whole store and rebuilds the index on a withheld row
severity: high
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "def lookup_many\("
  - path: plugin/daimon_briefing/index_locate.py
evidence:
  - note: "#1132 PR 11a: tests/test_view_lookup_many.py::test_an_id_nothing_holds_costs_no_session_parse_and_no_rebuild and tests/test_why_cost.py patch recall.rebuild, _rebuild_forced and _ensure_fresh to raise and run a lookup"
expires:
  condition: "recall.find stops calling _ensure_fresh and _rebuild_forced, or the locator is replaced by a bucket-local id table"
  review_after: 2027-04-09
status: candidate
---

`recall.find` calls `_ensure_fresh`, which stats every checkpoint, ledger and
team file on the machine and rebuilds the index when any moved, and it calls
`_rebuild_forced` when the newest row for the id is withheld. A `why` on a
quarantined id therefore rebuilt the whole index, and a `why` on an id nobody
holds paid for the stat of the whole store. `view.lookup_many` locates an id
with `index_locate.locate` instead: the index is opened `mode=ro`, the schema
version is checked, one indexed query runs per chunk of 500 ids, and the file
is never refreshed or written. The price is that an id serialized since the
last build is found through the pointer window only.

Do not "simplify" the locator into a call to `recall.find`, and do not add a
refresh to it. `recall.find` stays for `pending._loop_text`, which has its own
reasons. The value of a located item always comes from the named session file
through the view; the one exception is a row whose file is not local, which is
classified at read time before it is shown.
