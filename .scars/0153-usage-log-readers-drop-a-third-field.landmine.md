---
id: 153
type: landmine
title: usage.log readers keep only two-field lines, so a reason must be glued into the command token
severity: medium
confidence: 0.85
created: 2026-10-12
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/cli/stats.py
  - path: plugin/daimon_briefing/effects_commit.py
  - pattern: "worldcheck:[^\"' ]*skipped-"
evidence:
  - commit: 0128f8a
expires:
  condition: "the usage.log readers parse more than two whitespace fields"
  review_after: 2027-04-12
status: active
---

`_stats_usage` and the delivery-count reader in `cli/stats.py` split each
usage.log line and keep it only when it has exactly two fields
(`<iso> <command>`). A reason written as a third, space-separated field is
dropped silently: no error, the count just never moves. The skip reason of a
worldcheck claim therefore rides inside the command token,
`worldcheck:<class>:skipped-<reason>`, written beside the untagged
`worldcheck:<class>:skipped` line (#1181, 0128f8a).

Counting follows from that. An unanchored match on
`worldcheck:pr-state:skipped` also hits every tagged line and reads double.
Anchor on the whole token, for example the line ending in
` worldcheck:pr-state:skipped`. Do not replace the untagged line: it is a
documented-by-use `daimon stats` key.
