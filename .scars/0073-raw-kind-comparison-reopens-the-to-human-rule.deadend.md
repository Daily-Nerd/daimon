---
id: 73
type: deadend
title: "Comparing RAW stored kind values in the duplicate-opened branch reopens _kind_of's to_human rule; compare the post-gate value and gate on the duplicate's own authority instead"
severity: medium
confidence: 0.9
created: 2026-09-08
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - pattern: "_kind_of\(row, open_policies\) != founders\[q_id\]\[0\]"
violation: "row\.get\(.kind.\)\s*[!=]=|row\[.kind.\]\s*[!=]="
evidence:
  - commit: 636a1b7
  - pr: 972
  - note: #961 slice 1. The shipped disagreement rule fired unconditionally and compared post-gate values, so any non-human duplicate downgraded a human info ask (measured: an agent duplicate copying kind=info verbatim still forced work). Two candidate fixes were built and measured, both 5929 passed / 10 skipped, no existing test separating them.
  - note: "firing-review 2026-09-24: the anchor pattern was dead — the code moved to `_kind_of(row, open_policies) != founders[q_id][0]` (requests.py:608) while the anchor still read the pre-#1083 shape `_kind_of(row) != current['kind']`, so it never matched and `scar lint` flagged it as partial-rot. Refreshed the pattern to the live expression (verified it matches requests.py as committed). Armed `violation:` on a raw stored-kind comparison (`row.get(\"kind\")`/`row[\"kind\"]` against `==`/`!=`) that would bypass `_kind_of` the way the deadend variant did; confirmed healthy-when-absent against requests.py itself (0 matches) — the only file this scar is anchored to, so the only file the violation is ever evaluated against. Note for a future editor: the same regex DOES match unrelated `row[\"kind\"]`/`row.get(\"kind\")` checks in briefing.py, cli/lifecycle.py and several tests, which compare a checkpoint-item `kind` field, not a request's — harmless today only because those files carry no anchor for this scar; do not add one without re-checking this violation against them first."
expires:
  condition: "the founder's raw kind is retained on the record, so a raw comparison no longer needs side state in fold"
  review_after: 2027-03-01
status: active
---

Fixing the downgrade needs the `authority == "human"` guard on the duplicate row. It
does NOT need raw-value comparison, and adding that half is a deadend.

The variant that was tried: gate on the duplicate's authority AND compare the raw
stored `kind` strings rather than `_kind_of`'s post-gate values. It was implemented
and the full suite ran green, identical to the alternative. It fails on exactly one
input: a duplicate claiming `to_human: True` together with `kind: "info"`, the
combination `open_request` refuses outright. Raw comparison reads `"info" == "info"`,
calls it agreement, and lets the `to_human` claim fall on the floor. `_kind_of`
enforces `to_human` implies `work` for the founding row; the raw variant would not
enforce it for a contesting one. That split enforcement is the same shape as the
original defect it was fixing, where the write boundary guarded a rule the fold did not.

It also costs new mutable state: the founder's raw `kind` is not retained anywhere, so
a faithful implementation threads a side table through `fold`, whose docstring promises
determinism under reorder.

Keep the post-gate comparison. It fires on a superset of what raw fires on, every extra
firing is toward `work` on a row that contradicts itself, and there is no input where
raw is safe and post-gate is not.
