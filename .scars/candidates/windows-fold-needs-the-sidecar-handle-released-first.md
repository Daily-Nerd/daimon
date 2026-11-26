---
id: 0
type: landmine
title: A fold that unlinks or renames a held bucket must call jsonl.release_for_removal first, or it never completes on Windows
severity: medium
confidence: 0.7
created: 2026-11-25
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/buckets.py
  - path: plugin/daimon_briefing/jsonl.py
evidence:
  - note: "#1401: bucket merge refused on Windows for good; the lock primitive was missing, then the fold itself would have failed on the open sidecar handle"
expires:
  condition: "dir_lock opens the sidecar with FILE_SHARE_DELETE, or buckets stop removing a directory while holding its lock"
  review_after: 2027-05-25
status: candidate
---

A POSIX lock lives on the inode, so `buckets._retire` and the `migrate` rename
can unlink the held `.pointer.lock` and rename the directory around it, and a
waiter notices through `dir_lock`'s inode re-check (#1411). Windows refuses
both while any handle to the file is open (the builtin `open` does not pass
FILE_SHARE_DELETE). Unlink and rename raise PermissionError, `_retire` returns
"not removed", and the merge never completes.

So the holder gives the handle up right before the removal:
`jsonl.release_for_removal(dir)`. It is a no-op on POSIX (`pins_files` is
False). The exclusion ends there on Windows; a late writer either lands a row
the leftovers check sees or holds a handle that makes the removal fail and
leave the bucket standing. Any new code that removes or renames a directory
while inside `dir_lock` needs the same call. `tests/test_lock_windows_fold.py`
makes the filesystem refuse like Windows does, so a missing call fails on any
platform.
