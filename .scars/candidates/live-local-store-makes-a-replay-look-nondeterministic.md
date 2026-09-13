---
id: 0
type: landmine
title: A replay that reads ~/.daimon/checkpoints is not reproducible — other sessions append mid-run and it reads as runner nondeterminism
severity: medium
confidence: 0.95
created: 2026-09-12
authors: ["claude-code"]
anchors:
  - path: research/experiments/
expires:
  condition: "an experiment harness exists that snapshots and fingerprints its substrate by default"
  review_after: 2027-03-01
evidence:
  - note: "#976 replay — pass A and pass B were byte-identical, pass C twenty seconds later differed in 485 lines; the cause was four checkpoints written into ~/.daimon/checkpoints by other sessions between the passes, not the runner"
status: candidate
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
