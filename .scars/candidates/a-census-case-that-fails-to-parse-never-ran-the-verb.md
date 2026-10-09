---
id: 0
type: deadend
title: "A dashed bucket slug passed as a bare --to argument is an argparse error, so the request open census case never reached the verb"
severity: low
confidence: 0.9
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/tests/test_read_sentinel.py
evidence:
  - commit: f49ee80
  - note: "the case printed usage and exit 2, which its rc tuple allowed"
expires:
  condition: "argparse accepts a leading dash for a value that names a slug"
  review_after: 2027-04-09
status: candidate
---

A bucket slug starts with `-`, and `--to <slug>` reads the slug as an option. The `cli:request open` case
passed it that way, exited 2 with a usage block, and was allowed to by its rc tuple, so for as long as it
existed the census exercised nothing of the verb. Pass `--to=<slug>`.

A case whose rc tuple includes 2 can hide a parse error behind a refusal. When a case is meant to run a verb,
assert something only the verb prints (`shows=`), or check the uniform-failure test reaches a view call.
