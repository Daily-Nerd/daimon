---
id: 0
type: landmine
title: A bucket is born by renaming a stage, and rename onto an empty directory replaces it
severity: high
confidence: 0.85
created: 2026-11-22
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/identity.py
  - pattern: "os\.rename\("
evidence:
  - commit: ee0a99e
  - note: "POSIX rename(2) onto an EMPTY existing directory succeeds and replaces it, measured on macOS by the slice 4 refuters; onto a non-empty one it raises ENOTEMPTY, a plain OSError"
expires:
  condition: "birth stops using rename, or an atomic create-directory-with-contents primitive replaces it"
  review_after: 2027-05-22
status: candidate
---

A new bucket is built whole in `.birth/<pid>-<rand>/` with its `root` inside and
then renamed to its final name, so no bucket is ever visible without the record
that says whom it belongs to. Three things in that one `os.rename` look
fixable and are not.

`lexists(<name>)` is checked BEFORE the rename: renaming onto an empty
directory silently replaces it, and an empty seed directory is exactly what an
older installed daimon leaves for an instant (it makes the directory, then
stamps). After the call only ENOTEMPTY, EEXIST and FileExistsError mean "taken".
PermissionError (an open handle on NTFS) and everything else is retried three
times and raised as `BucketClaimFailed`; treating it as taken would hand the
location to the next name with no error, and treating it as success would not
create the bucket.

Tests that patch `os.rename` globally to simulate EXDEV for `migrate` also hit
the birth rename, because the module is shared. Patch selectively: let a path
under `.birth` through (tests/test_bucket_migration.py).

Do not swap the rename for `mkdir` plus a root write: that is the window the
stage exists to close.
