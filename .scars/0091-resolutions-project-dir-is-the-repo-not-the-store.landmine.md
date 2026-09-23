---
id: 91
type: landmine
title: store.resolutions(project_dir=...) takes the REPO path, not the store dir — handed the store dir it returns {} with no error
severity: medium
confidence: 0.9
created: 2026-09-15
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "resolutions\(project_dir"
evidence:
  - issue: 1035
  - note: #1035 replay. An ad-hoc script replayed 45 real checkpoint hops through carry.merge at two caps and passed project_dir=~/.daimon/checkpoints/<slug> — the directory events.jsonl actually lives in, 356 KB of it. store.resolutions returned 0 rows. No exception, no warning. _resolved() had re-slugged that path into a slug of its own and looked for events.jsonl under a second, nonexistent directory. Passing the repo working dir instead returned 152 resolved refs. Both runs printed a confident table; the wrong one merged with an empty resolved set, so every closed item stayed carried, and the numbers differed (82,946 vs 83,966 final bytes, 0 vs 3 items past the staleness budget).
expires:
  condition: "store.resolutions rejects or detects a project_dir that is already inside DAIMON_CHECKPOINT_DIR, or _resolved() stops re-slugging an absolute store path"
  review_after: 2027-03-15
status: active
---

Every `project_dir=` argument on the store API is the PROJECT working
directory (the repo you are in). `_resolved()` slugs it and joins it under
`DAIMON_CHECKPOINT_DIR`. So handing it the per-project STORE directory, the one
that visibly contains `events.jsonl`, is not a shortcut to the same place: it
gets slugged a second time and resolves to a path that does not exist.

`store.resolutions()` reads that path through a `try/except OSError` that
returns `{}` on a missing file, because a reader must never drop the log over
one bad line. The same guard makes a wrong-directory call indistinguishable
from a project that has resolved nothing. There is no error to notice.

This is the ad-hoc-analysis-script hazard scar 0036 describes, in a different
input: the numbers come back, they look plausible, and they measure a
configuration that does not exist. `carry.merge` with an empty `resolved` set
carries items a human already closed, which inflates every survival and size
figure in the same direction, so nothing looks wrong internally.

Before trusting any store read in a script, print the count. A zero from
`resolutions()`, `is_resolved()` or any other `project_dir=` reader on a
project you know has history means the path was wrong, not that the history
was empty. Pass the repo path and check the number moves.
