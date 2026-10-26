---
id: 0
type: fence
title: An overturned record without a reason reads "overturned" as a refutation and "retired" as a ruling; the two defaults differ on purpose and are never backfilled
severity: medium
confidence: 0.85
created: 2026-10-26
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/cli/_ledger.py
  - path: plugin/daimon_ui/static/render.js
  - path: plugin/daimon_briefing/refutations.py
  - pattern: "overturn_reason"
evidence:
  - note: "issue #1238; plugin/tests/test_refutations.py::test_an_overturned_row_without_a_reason_folds_to_none_and_reads_as_before and plugin/tests/test_rulings.py::test_a_retired_row_without_a_reason_still_reads_retired pin both fallbacks"
expires:
  condition: "every overturned row in every ledger carries a reason, or the fold stops distinguishing rows that predate the field"
  review_after: 2027-04-26
status: candidate
---

Since #1238 the `overturned` and `overturn-proposed` rows carry `reason`,
"rejected" for a candidate turned down or "retired" for an active record that
stopped applying, decided inside `refutations._close_reason` from the state at
write time. Rows written before that fold to `overturn_reason: None`.

Two things about the readers look like an inconsistency and are not. First,
`_ledger._shown_state` and the two `render.js` markers take a different default
for a None reason: a refutation keeps "overturned", a ruling keeps "retired".
Those are the two words each printer used before the field existed, so an old
ledger renders exactly as it did; unifying them changes the label on records
nobody rewrote. Second, the fold does not infer a reason for an old row from
its neighbours (a prior `ratified` row would suggest "retired"). The state the
row closed is not in the row, and a guessed reason is a claim about a human
decision the ledger never recorded. Backfilling or inferring either is a
behaviour change on existing data; if it is ever wanted it needs its own
decision, not a cleanup.
