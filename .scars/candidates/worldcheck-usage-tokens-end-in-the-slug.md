---
id: 0
type: landmine
title: worldcheck class usage tokens end in the project slug, so count them through parse_usage_token, never by line ending
severity: medium
confidence: 0.85
created: 2026-10-17
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/worldcheck.py
  - path: plugin/daimon_briefing/cli/stats.py
  - pattern: "endswith\(. worldcheck:"
evidence:
  - commit: 9d203e7
expires:
  condition: "usage.log lines carry the project in a field of their own"
  review_after: 2027-04-17
status: candidate
---

Since #1184 the per-class counters in usage.log are
`worldcheck:<class>:<outcome>:<slug>` (skipped-<reason> included); lines written
before carry no slug, and the aggregates (`worldcheck:confirmed`) never do. The
slug rides inside the one command token, like the receipt-probe counters, so
the line keeps two whitespace fields (scar 0153). A line-ending match such as
`ln.endswith(" worldcheck:pr-state:confirmed")` therefore misses every new
line without any error: the count just stops moving. The anchoring advice in
0153 still holds against `skipped-` double counting, but compare the token
after `worldcheck.parse_usage_token(...).key`, which drops the slug.

A reader that wants one project's checks compares `.slug` and must treat
`None` as unscoped (counted for no project), not as a match. The research
script for #1183 (`research/experiments/worldcheck-per-note-1183/measure.py`)
once read these lines with a full-match regex that had no slug; it now goes
through the parser too.
