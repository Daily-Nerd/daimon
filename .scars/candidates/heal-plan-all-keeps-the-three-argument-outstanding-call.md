---
id: 0
type: landmine
title: heal.plan_all keeps the three-argument ledger._compute_outstanding call and routes presumed_dead through _scan_outstanding
severity: medium
confidence: 0.8
created: 2026-10-15
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/heal.py
  - pattern: "_compute_outstanding\(text, now"
evidence:
  - note: "tests/test_cli.py test_heal_plan_* replace ledger._compute_outstanding with fakes of the exact signature (text, now, force=False); #1179 PR 3 (feat/heal-all)"
expires:
  condition: "the test_heal_plan_* fakes accept **kwargs or are rewritten over heal.plan_all"
  review_after: 2027-04-15
status: candidate
---

`ledger._heal_plan` is now written over `heal.plan_all`. The old
`test_heal_plan_*` tests (and the `cli._heal_plan` fakes in
tests/test_admission_refusal.py) stub `ledger._compute_outstanding` with
`lambda text, now, force=False: items`, and
tests/test_admission_ledger.py::test_compute_outstanding_keeps_its_signature
pins the parameter list to `["text", "now", "force"]`. Adding a
`presumed_dead` parameter to it (the first attempt) fails that pin, and a
`plan_all` that always forwarded the keyword raises TypeError inside every one
of those fakes. The fakes build their items through tests/_heal_items.py, which has the shape the classifier emits.

So `plan_all` calls `_compute_outstanding(text, now, force=force)` unless a host
asserted `presumed_dead_before`, and only then goes through
`_scan_outstanding(..., presumed_dead=...)`, which `_compute_outstanding`
wraps. It reads `kind`, `age` and `retried` as required keys of every healable item.
Do not collapse the two paths into one call that always forwards the predicate;
the design (D12) requires those fakes to pass unchanged.
