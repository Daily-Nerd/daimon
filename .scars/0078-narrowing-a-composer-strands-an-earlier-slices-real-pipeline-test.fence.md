---
id: 78
type: fence
title: Narrowing a shared composer strands an earlier slice's "built through the real pipeline" test — rewrite it against a synthetic row, don't weaken the assertion
severity: medium
confidence: 0.8
created: 2026-09-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/pending.py
  - path: plugin/tests/test_cli_decide.py
  - path: plugin/tests/test_pending.py
evidence:
  - note: #961 slice 3, feat/961-slice3-agent-accept-info
expires:
  condition: "pending.py's request lane and lifecycle._decide_cards are merged into one function, or the queue-row shape stops carrying a synthesizable dict"
  review_after: 2027-03-01
status: active
---

#961 slice 2 shipped three tests whose docstrings explicitly argue a synthetic
row is the WEAKER proof and insist on driving `kind="info"` through the real
`pending.queue` -> `lifecycle._decide_cards` pipeline
(`test_decide_card_shows_the_info_marker_for_an_info_request`,
`test_decide_card_lane_tag_is_never_overloaded_by_the_approval_kind`,
`test_decide_card_with_the_info_marker_still_gets_ledger_header_spans`).
Slice 3 added one line to `pending._request_rows` excluding
`kind == "info"` from the request lane entirely. That line made all three
tests fail outright (not silently — `pending.queue` no longer returns the
row at all), because the scenario their own docstrings argued for is no
longer reachable through the shipped pipeline.

The fix was not to delete the coverage: `_decide_cards` itself is unchanged,
generic code that still has to render the marker/lane-tag/header-span shape
correctly if handed a row with `approval == "info"` — the file's own
`test_decide_cards_lane_guard_is_load_bearing_not_dead_code` already
established the "hand `_decide_cards` a synthetic row" pattern for exactly
this reason (a future bug elsewhere leaking `approval` onto a non-request
lane), so the other three tests were rewritten to use it too, with a
docstring note on why the real pipeline no longer applies. Full record in
that commit's diff to `plugin/tests/test_cli_decide.py`.

A future editor narrowing what a shared composer (`pending.queue`,
`requests.recipient_join`, `requests.inbox_renderable`, …) returns must grep
the CONSUMERS of that composer for tests whose docstrings claim "built
through the real pipeline, not hand-typed" — those are the ones a narrowing
change silently invalidates, and running the full suite is what catches it
(it did here: 3 failures on the first full run after the pending.py change).
Rewrite them against a synthetic row shaped like the composer's own row
builder, matching the pattern this file already established — never just
delete the assertion or loosen it to pass again.
