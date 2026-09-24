---
id: 88
type: landmine
title: A replay that reads ~/.daimon/checkpoints is not reproducible — other sessions append mid-run and it reads as runner nondeterminism
severity: medium
confidence: 0.95
created: 2026-09-12
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: research/experiments/trust-gate-cost-976/
  - path: research/experiments/refutation-guard-precision/
  - path: research/experiments/recall-replay-ab/
  - path: research/experiments/ungated-arm/
  - path: research/experiments/merge-fidelity-536/
  - path: research/experiments/multicycle/conftest.py
  - path: research/experiments/multicycle/run_multicycle.py
evidence:
  - note: #976 replay — pass A and pass B were byte-identical, pass C twenty seconds later differed in 485 lines; the cause was four checkpoints written into ~/.daimon/checkpoints by other sessions between the passes, not the runner
  - note: "firing-review 2026-09-24: the bare `research/experiments/` path matched 325/948 tracked files (34%), most of them one-off analysis scripts and result dumps that never touch the live checkpoint store. Replaced it with the specific harness directories confirmed to read `~/.daimon/checkpoints` or honor `DAIMON_CHECKPOINT_DIR` (grepped repo-wide, then narrowed to research/experiments/): trust-gate-cost-976 (the #976 replay itself), refutation-guard-precision, recall-replay-ab, ungated-arm, merge-fidelity-536, and two individual files inside multicycle/ (conftest.py, run_multicycle.py) rather than the whole multicycle/ prefix, which would have pulled in its 242-file results/ dump for zero extra coverage. New total: 24/948 files (2.5%). A repo-wide content pattern was considered and rejected: `\\.daimon/checkpoints|DAIMON_CHECKPOINT_DIR` alone hits 64/948 files, and most of those are tests deliberately overriding DAIMON_CHECKPOINT_DIR to a tmp dir, the project's standard way to keep manual/CLI runs off the real store — the SAFE pattern, not the hazard — so a bare content anchor would have fired backwards."
expires:
  condition: "an experiment harness exists that snapshots and fingerprints its substrate by default"
  review_after: 2027-03-01
status: active
---

The bench substrate under `benchmark/.work/` is frozen, so the #754 replay's
copy-on-read discipline is enough for it. The local checkpoint store is not.
`~/.daimon/checkpoints` is the machine's live store: every other agent session,
every `daimon ruling`, every SessionEnd capture appends to it while your replay
is running. Two passes minutes apart read two different corpora.

This does not look like a moving substrate. It looks like a nondeterministic
runner, and it sends you hunting for unsorted iteration or a hash seed inside
code that is in fact perfectly deterministic. In #976 the first two passes were
byte-identical, which made the third pass's 485-line diff read as a genuine
instability; the actual cause was a `latest.json` rewrite and a new
`refutations.jsonl` row written by unrelated sessions in the gap.

Copy-on-read does not solve it either. Copying the store into scratch protects
the ORIGINAL, which is a different problem; the copy still captures whatever
the store happened to hold at copy time.

What a future editor must do: freeze the local store into a snapshot ONCE,
point the runner at the snapshot for every pass, and record a fingerprint of
the files actually read in the committed output. Then a later re-run can tell
"the code changed" from "the store moved" instead of guessing. `#976`'s
`fingerprint_store` and the `inputs` block in its `measurements.json` are the
shape. Also beware the mirror trap when auditing the store afterwards: a
before/after `stat` diff over the live store shows writes you did not make, so
take the two snapshots as close around the run as possible or you will spend
the audit proving a clean runner dirty.
