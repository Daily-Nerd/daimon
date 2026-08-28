---
id: 0
type: deadend
title: Blanket fallback=False on a reporting surface refuses UN-ROUTED checkpoints too
severity: medium
confidence: 0.9
created: 2026-08-28
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/receipts.py
  - path: plugin/daimon_briefing/store.py
evidence:
  - note: "#791 — tried, broke test_cli_verify_receipt_default_uses_latest, replaced"
expires:
  condition: "un-routed (project_slug-less) checkpoints no longer exist in any store"
  review_after: 2027-02-28
status: candidate
---

Fixing a cross-project read by passing `fallback=False` is right for callers that
PERSIST (#94, #789) and for display callers that can fall back to a rulings-only
or header-only path (#785, #788). It was tried at the two receipt surfaces and is
WRONG there.

`read_latest`'s global pointer holds three different things, not two. Besides the
project's own checkpoint and another project's, there are UN-ROUTED checkpoints,
written before a project was known, carrying no `project_slug` stamp. They belong
to nobody. `fallback=False` refuses those as well, so a pre-routing store loses
`verify-receipt` and the receipts status line entirely, and the surface reports
"no checkpoint for this project yet" while a usable file sits in the store.

The distinction is available: decide by the payload's own `project_slug`, which
is what team fan-in already does, not by which pointer the read came through.
That is `store.read_latest_reportable`. Every new test written for the defect
passed under the blunt version; only the pre-existing suite caught it.
