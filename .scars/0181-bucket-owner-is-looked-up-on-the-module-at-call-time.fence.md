---
id: 181
type: fence
title: store.bucket_of is called through the module at call time, never imported by name, so one rebinding moves every writer
severity: medium
confidence: 0.8
created: 2026-11-22
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
promoted_from: candidates/bucket-owner-is-looked-up-on-the-module-at-call-time.md
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/tests/test_bucket_owner_lock_in.py
evidence:
  - commit: 17b44b9
expires:
  condition: "the owner is passed in explicitly instead of looked up as store.bucket_of"
  review_after: 2027-05-22
status: active
---

The lock-in test proves one owner decides every bucket name by replacing
`store.bucket_of` with a function that binds a project to another bucket, then
running every writer and every ledger-path builder. That only works because
every site reaches the owner as `store.bucket_of(...)` (or through
`bucket_dir`, `project_bucket`, `ledger_file`), looked up on the module when it
runs.

`from .store import bucket_of` copies the function at import time. The site
keeps the old binding after a rebinding, writes under the old slug, and the
lock-in test still looks at the new one: the site is invisible to it. The same
goes for deriving a name with `project_slug(location)` or joining
`checkpoint_dir() / <slug>` by hand; tests/test_bucket_owner_census.py fails on
both shapes. Add a new bucket-naming site the same way, through the module.
