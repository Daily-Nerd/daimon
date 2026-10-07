---
id: 0
type: landmine
title: An events.jsonl row can carry the content key of a forgotten value (a tombstone's status, a scrub marker in note, item_text or status); a reader that prints the row prints the key
severity: high
confidence: 0.85
created: 2026-10-07
authors: []
anchors:
  - path: plugin/daimon_briefing/view.py
  - pattern: "def _event\("
evidence:
  - note: "#1132 PR 8b-2: view.events collapses a tombstone status to the bare word forgotten and reads a scrubbed field as absent; tests/test_view_events.py and tests/ui/test_viewer_events.py pin that the key is in no row of the activity feed"
expires:
  condition: "forget stops writing the key into events.jsonl (tombstone status and scrub marker), or every events reader goes through view.events"
  review_after: 2027-04-07
status: candidate
---

A forget writes a tombstone row whose `status` is `forgotten:<content key>`,
and `store.scrub_event_fields` later replaces any field of an older row that
held the value with `[forgotten:<content key>]` (a `status` keeps its class
token and gets the marker appended). The key is the hash of the value, so it
names what was forgotten: showing it tells a reader that the value existed and
lets them test a guess against it. The CLI never prints it.

`view.events` is the one place an events row becomes text for a reader. A
tombstone is the bare status `forgotten` with no item text, a field holding the
scrub marker is absent, and the prose columns (`note`, `item_text`, `status`)
go through `prose_verdict` for quarantine. A new reader of `events.jsonl` that
parses the rows itself, or that formats `status` as it finds it, brings the key
back. The sentinel looks for the sentinel BYTES of a value, not its key, so it
does not catch this.

If you need a new field from an event row, add it to `view.Event` and judge it
in `_event`. If you need rows for a new surface, call `view.events`.
