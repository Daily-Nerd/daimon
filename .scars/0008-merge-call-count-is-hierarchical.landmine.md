---
id: 8
type: landmine
title: serializer merge-call count is hierarchical (chunks × merge_group_size) — never assert == 1
severity: medium
confidence: 0.85
created: 2026-06-30
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/serializer.py
  - pattern: "MERGE_SYS"
evidence:
  - commit: 6aa7844  # squash-merge of PR #70
  - pr: 70
expires:
  condition: "merge_partials stops folding hierarchically (single-pass merge restored)"
  review_after: 2027-06-30
status: active
---

`serializer.merge_partials` folds partial checkpoints HIERARCHICALLY: it splits
partials into consecutive groups of K = `config.merge_group_size()` (default 3,
capped small because 6-chunk merges DNF at 900s — issue #28) and repeats
`while len(partials) > 1`. So the number of MERGE_SYS LLM calls is NOT 1 — it is
a function of chunk count × K across one or more levels (e.g. 5 partials → 2
merges → 1 merge = 3 calls).

`test_rerun.py::test_serialize_chunked_over_threshold` originally asserted
`len(merge_calls) == 1`; that predated grouped merge and went red on main once
enough chunks existed (PR #70 fixed it to `>= 1` + pinned DAIMON_MERGE_GROUP_SIZE
for determinism).

If you write a test that counts merge calls, pin `DAIMON_MERGE_GROUP_SIZE` and
`DAIMON_CHUNK_LINES`, and assert `>= 1` (or compute the expected hierarchical
count) — never a bare literal. A single-pass assumption is wrong by design.
