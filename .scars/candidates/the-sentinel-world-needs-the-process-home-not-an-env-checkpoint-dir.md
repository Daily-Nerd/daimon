---
id: 0
type: landmine
title: "Running the read census with DAIMON_CHECKPOINT_DIR set in the shell breaks the module-scoped world fixture"
severity: medium
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/tests/_sentinel_world.py
  - path: plugin/tests/test_read_sentinel.py
evidence:
  - note: "11c baseline: with the variable exported the census saw 4 extra leaks and the health fixture errored; unset, 39 and 7 tests pass"
expires:
  condition: "the world fixture pins its own checkpoint dir"
  review_after: 2027-04-09
status: candidate
---

`world_run` and `health_runs` are module-scoped, so the function-scoped autouse isolation in conftest never
applies while they build and drive the world. The world isolates itself through `HOME` only. A
`DAIMON_CHECKPOINT_DIR` exported by the shell (a sensible habit around manual CLI runs) is then the store the
world writes into, every case shares one directory across the restores, and the census reports leaks that are
not there (and errors in the health fixture).

Run the sentinel files with that variable unset. Keep it for manual `daimon` runs against a scratch store.
