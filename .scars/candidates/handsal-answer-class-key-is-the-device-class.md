---
id: 0
type: landmine
title: A handsal checker answer's `class` key is the signing device class, never the reason class
severity: high
confidence: 0.9
created: 2026-11-21
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/authority.py
  - path: plugin/daimon_briefing/_handsal/
evidence:
  - commit: dab7cdb
expires:
  condition: "handsal's checker answer carries the reason class itself, so the vendored table is no longer needed"
  review_after: 2027-05-21
status: candidate
---

`handsal-check recheck-act` answers `{class, not_proven, reason, receipt_id,
rechecked}`. `class` there is the class of the signing DEVICE (for example
`apple-se-bio`), not the reason class. Reading it as a reason class gives
nonsense that can still look plausible. The reason class comes only from
handsal's `reason-classes.json`, looked up by `reason`, the command's own
`by_command` entry first and `default` after it. That is why the table is
vendored: no installed file holds it.

Rules that keep the copy honest: one pin (commit and sha256) in
`authority.py`, the packaged file must hash to it, and a pin bump must not
change or drop any `(command, code)` pair. `ok` is proven only with
`rechecked` exactly `True`. Do not add a second reader of the table; the
census test fails on one.
