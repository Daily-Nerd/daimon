---
id: 141
type: landmine
title: A forget step must return its own reach, judged by a read afterwards, never a bare list or count
severity: high
confidence: 0.85
created: 2026-10-10
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/forget.py
  - path: plugin/daimon_briefing/store.py
evidence:
  - commit: 45c9217
expires:
  condition: "every deleter returns a jsonl.Reached and the registry census rejects any other shape"
  review_after: 2027-04-10
status: active
---

`store.scrub_content_key`, `store.scrub_team_copies` and
`recall.refresh_store_caches` swallow every failure unless they report their
own reach: returning only the paths they rewrote makes a read-only directory, a
full disk or a torn JSON surface look like "reached, nothing removed". Exit 4
for a non-ledger step means nothing under that shape, and a test that patches
the deleter to raise passes while the real failure stays invisible (scar 0135,
applied to the steps that scar did not reach).

A step returns a `jsonl.Reached` built from a read of the surface AFTER the
step: a copy that still holds the value, or that cannot be parsed to prove it
does not, is unreached. `forget.run` accepts a fixed set of result shapes and
reports anything else as unreached. Test a failing step with a directory made
unwritable by `chmod`, never with a monkeypatched raise.
