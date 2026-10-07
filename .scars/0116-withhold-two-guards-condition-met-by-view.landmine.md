---
id: 116
type: landmine
title: briefing.withhold is now a compatibility wrapper over view.classify and view.closing_event, so scar 0108's two early-return guards no longer carry a quarantine pool
severity: low
confidence: 0.8
created: 2026-10-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - pattern: "def withhold"
evidence:
  - note: #1132 PR 7a: the quarantine and resolution branches moved to view (classify, closing_event, live); withhold keeps one guard and exists only for status --suppressed and its tests until PR 7b.
expires:
  condition: "withhold is deleted with the status --suppressed conversion (PR 7b)"
  review_after: 2027-04-07
status: active
---

Scar 0108's expiry condition ("withhold() is refactored to a single dispatch
table over named pools") is met in effect: `withhold` no longer matches a
quarantine pool itself. Its one remaining no-op guard checks the three
arguments it still honors (`resolutions`, `amendments`, `quarantine`) and
hands the decisions to `view`. A human reviewer decides whether to archive
0108 now or when the wrapper is deleted.

If you add a new reason to withhold an item, add it to `view.classify`
(precedence closed, forgotten, quarantine) and its twin tests; do not teach
the wrapper a new branch. Test the wrapper with the new argument alone, as
0108 asks, until it is gone.
