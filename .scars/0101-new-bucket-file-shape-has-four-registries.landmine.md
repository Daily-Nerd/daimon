---
id: 101
type: landmine
title: A new file shape inside checkpoints/{slug}/ must be registered in FOUR places, not one
severity: high
confidence: 0.9
created: 2026-09-23
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/surfaces.py
  - path: plugin/daimon_briefing/buckets.py
  - path: plugin/daimon_briefing/privacy.py
  - path: plugin/tests/test_store_resolution.py
evidence:
  - note: #1092 store.record_bucket_root() adding checkpoints/{slug}/root broke 23 tests across test_audit_privacy.py, test_write_audit_guard.py, test_bucket_migration.py, and test_store_resolution.py before all four were updated
expires:
  condition: "the four registries below are merged into one derived view"
  review_after: 2027-03-01
status: active
---

Adding `store.record_bucket_root()`, which drops a new plain file
(`checkpoints/{slug}/root`) inside every bucket directory, passed its own unit
tests cleanly but broke 23 tests elsewhere in the suite with no obvious
connection to the change. Each failure came from a DIFFERENT hand-maintained
list that walks a bucket directory and asserts it recognizes every file in it:

1. `surfaces.py` `SURFACES`, the declared-surface registry. A shape not
   declared here fails `tests/test_write_audit_guard.py`
   (`test_every_observed_write_shape_is_declared`) and is NOT exempted from
   `privacy.py`'s plaintext scan (`_EXEMPT_NAMES` is a derived view of
   `audit_exempt=True` entries here, so declaring it here also fixes
   `test_audit_privacy.py`, but only for FIXED-NAME files with no wildcard
   in the last path segment).
2. `buckets.py` `_REMOVABLE`, a SEPARATE hardcoded set of non-ledger
   filenames a legacy-bucket migration may delete without treating them as
   unexplained "leftovers" that block `complete: true`
   (`test_bucket_migration.py`).
3. `tests/test_store_resolution.py` `_public_entry_points()`, a third
   hand-maintained list, cross-checked by AST against every public
   `store.py` function whose signature literally contains an arg named
   `project_dir` (not `project_dir_or_slug`: name it differently on
   purpose if the function must NOT resolve through
   `config.resolve_project_dir`, as `store.bucket_root` does deliberately).
4. `store.py`'s own writer must call the new record function AT EVERY site
   that creates the bucket directory, not just one. A bucket can be born by
   `write_checkpoint`, `append_event`, `append_verification`,
   `record_forget_hits`, or `refutations.append`/`_write_policy_tombstones`,
   and each is an independent "first write" candidate.

A future new bucket-level file (another marker, another sidecar) must touch
all four, in this order: declare in `surfaces.py` first (exempt_names/
exempt_suffix are DERIVED from it), then `buckets.py` `_REMOVABLE` if a
legacy-bucket merge should discard it, then `test_store_resolution.py` if the
writer is a new public `store.py` function taking `project_dir`. Grep for
existing entries in each list for the copy-paste shape before assuming one
declaration is enough.
