---
id: 0
type: fence
title: ledger repair --dry-run runs the real repair on a scratch copy; a deleter that writes outside the bucket escapes it
severity: medium
confidence: 0.8
created: 2026-10-05
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/ledger_repair.py
  - pattern: _scratch_store
evidence:
  - note: "#1132 stage 2c-2: the single-key scrub has no dry-run mode (only trust.redact_content_key does), so --dry-run copies the bucket's ledger files to a temp dir and overrides config.checkpoint_dir() to it, for the calling context only"
expires:
  condition: "every deleter in scrub_forgotten_key takes a dry_run flag and repair no longer stages a scratch copy"
  review_after: 2027-04-01
status: candidate
---

`daimon ledger repair --dry-run` must report the counts the real run would
produce, and the five ledger deleters (events, refutations, relations,
amendments, requests) only know how to write. So the dry run executes the real
code against a throwaway copy of the bucket's `*.jsonl` and sidecar files, with
`config.checkpoint_dir()` overridden to it through a context variable
(`config.checkpoint_dir_override`, set by `_scratch_store`), so the redirect
holds for the calling context only and concurrent readers in the same process
still see the real store.

Reach is exactly the files copied. A deleter that starts writing anything else
under the bucket (or outside the checkpoint dir) is not covered: the dry run
would write it for real. When adding a deleter to `scrub_forgotten_key`, either
give it a dry-run flag or make sure the scratch copy includes every file it
touches. `test_dry_run_reports_the_same_counts_and_writes_nothing` is the
bytes-equal check that catches the first kind of escape.
