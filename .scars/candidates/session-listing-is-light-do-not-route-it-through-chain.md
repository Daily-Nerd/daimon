---
id: 0
type: fence
title: view.sessions and view.pointers list sessions without copying or classifying bodies; routing a listing through view.chain multiplies its cost
severity: medium
confidence: 0.8
created: 2026-10-07
authors: []
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "def sessions\("
evidence:
  - note: "#1132 PR 8b-1 measurement on a 50-bucket tmp store (30 sessions per bucket): view.sessions 8.8 ms, view.chain full 18.3 ms; on a copy of a real 36-bucket store the full chain measured about 4x a plain file listing of the same sessions (568 ms against 143 ms)"
expires:
  condition: "view.chain gains a parse cache keyed by (path, st_ino, mtime_ns, size) and a measurement shows the full chain within 1.5x of the listing"
  review_after: 2027-04-07
status: candidate
---

`view.sessions(project)` returns one row per session file (file stem, `created`,
the topic a reader may see) plus a count of files that do not parse. It
classifies the topic of each session and nothing else: no `copy.deepcopy`, no
`_filter`. `view.chain` and `view.open_sessions` do the full judgement of every
item and are the right call when a body is needed.

The history, checkpoints and diff-default routes need only ids, stamps and
topics, and the page asks for them on every navigation. Building them from
`chain` pays for a deep copy and a classification of every item of every
retained session (about 320 of the 570 ms measured on the real store) and for
`store.project_surfaces`, which parses every JSON file of every bucket to decide
membership.

If a listing needs one more field, add it to `SessionRow` from the parsed
envelope. If it needs an item, ask `open_sessions` for the few sessions it
shows. A parse cache for `_chain_raw` is the other way out; it must be keyed by
(path, st_ino, mtime_ns, size) and thread-safe, because the viewer serves each
request on its own thread.
