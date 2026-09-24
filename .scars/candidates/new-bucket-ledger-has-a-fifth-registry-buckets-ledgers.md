---
id: 0
type: landmine
title: A new plaintext bucket ledger has a FIFTH registry beyond scar 0101's four — buckets.LEDGERS, cross-checked by test_bucket_migration.py
severity: high
confidence: 0.9
created: 2026-09-24
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/buckets.py
  - path: plugin/tests/test_bucket_migration.py
  - path: plugin/daimon_briefing/privacy.py
evidence:
  - note: "#1109 Slice 1 (trust.jsonl): declaring the new ledger in surfaces.py alone left two things broken until fixed by hand — test_bucket_migration.py::test_every_bucket_ledger_shape_the_registry_declares_is_merged (asserts every checkpoints/{slug}/*.jsonl surfaces.py entry is also in buckets.LEDGERS) failed immediately, and privacy.audit_project would have classified the file as unknown -> unscannable -> exit 3 on the FIRST write, repeating #645's own documented history, because nothing in scar 0101 mentions privacy.py's hand-maintained _checkpoint_candidates() exclusion tuple or its per-ledger scan block."
expires:
  condition: "buckets.LEDGERS is derived from surfaces.SURFACES instead of hand-maintained, and privacy.py's per-ledger scan blocks are replaced by one generic loop over a SURFACES-derived plaintext-ledger list"
  review_after: 2027-03-01
status: candidate
---

Scar 0101 names four registries for a new file shape under
`checkpoints/{slug}/`: `surfaces.py` `SURFACES`, `buckets.py` `_REMOVABLE`,
`tests/test_store_resolution.py` `_public_entry_points`, and
`store.record_bucket_root` call sites. Adding `trust.jsonl` (#1109 Slice 1)
found two more things that break, neither covered by that scar:

1. `buckets.py` also has a `LEDGERS` tuple (separate from `_REMOVABLE`) that
   a legacy-bucket migration walks to know which files are mergeable
   append-only ledgers. `tests/test_bucket_migration.py::
   test_every_bucket_ledger_shape_the_registry_declares_is_merged` asserts
   `declared == set(buckets.LEDGERS)` where `declared` is every
   `checkpoints/{slug}/*.jsonl` shape in `surfaces.SURFACES` — so declaring
   a new `.jsonl` ledger in `SURFACES` without adding it to `LEDGERS` fails
   this test immediately, by design (its own docstring: "A new bucket
   ledger that lands there and not in LEDGERS would be left behind by every
   migration, silently").
2. `privacy.py` hand-maintains a THIRD list: a tuple of known ledger
   filenames inside `_checkpoint_candidates()`'s bucket walk (excluding them
   from the generic "unknown file" classification), plus a bespoke
   read-and-hash-intersect block per ledger inside `audit_project`. Neither
   is a scar-0101 registry, and surfaces.py's own docstring even says
   privacy's exemptions are "derived" from it — true only for
   `audit_exempt` fixed-name files, NOT for a `plaintext=True, delete=
   "rewrite"` ledger like `refutations.jsonl`/`amendments.jsonl`/
   `trust.jsonl`. Skipping this wiring reproduces #645 exactly: the file
   lands in `unknown`, then `unscannable`, pinning every `daimon audit
   privacy` run on that project at exit 3 from the first write onward — no
   test failure signals this ahead of time, because none of
   `test_audit_privacy.py`'s existing fixtures write the new file.

A future new bucket ledger must ALSO touch: `buckets.LEDGERS` (or the merge
test explains why), and `privacy.py`'s `_checkpoint_candidates` exclusion
tuple plus a scan block in `audit_project` mirroring the amendments/
refutations one — verified with a live `privacy.audit_project` call after
one write, not just by reading the registries.
