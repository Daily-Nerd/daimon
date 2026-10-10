---
id: 0
type: landmine
title: A numeric field read from a teammate's file reaches int() in the fold, json accepts Infinity and NaN, and a view that fails open un-hides everything
severity: critical
confidence: 0.9
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/policy.py
  - pattern: "_published_order"
evidence:
  - note: tests/test_quarantine_fold.py::test_a_non_finite_or_foreign_order_folds_as_zero
  - note: tests/test_team_quarantine_worlds.py::test_a_poisoned_file_leaves_every_other_claim_in_force
expires:
  condition: "published rows are parsed by a strict decoder that refuses non-finite numbers before any fold sees them"
  review_after: 2027-04-09
status: candidate
---

Python's `json` reads `Infinity`, `-Infinity`, `NaN` and `1e999` as valid
numbers, so `jsonl` hands such a row to the fold as an ordinary dict. The
quarantine fold sorted on `int(row["order"])`, and `int(inf)` raises
OverflowError. That raise left `foreign_team` and then `view.snapshot`, which
caught it and opened with EMPTY sets: one forged line in one author's file
un-hid every forgotten and quarantined value on the machine, not only that
author's.

Three layers now hold, and a new numeric field must keep all three. The
reader takes only a finite number (anything else folds as 0 like a missing
order) and ignores a row whose other fields are off shape. `foreign_team`
reads and folds each author on its own, so a raising author is unproven and
the rest still apply. A snapshot that cannot get the teammates' sets closes,
never opens. Every numeric or free-text field from a teammate's file needs the
same finite or shape check before it is sorted, indexed or printed.
