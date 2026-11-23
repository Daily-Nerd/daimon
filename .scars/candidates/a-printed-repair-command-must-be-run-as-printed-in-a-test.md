---
id: 0
type: landmine
title: A repair command a verb prints for a person must be parsed back and run as printed in a test, because the verb it names may refuse it
severity: medium
confidence: 0.85
created: 2026-11-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/bucket_tools.py
  - pattern: "collision_merge_command"
evidence:
  - commit: 6ccf3fb
  - note: "Found by running the machine-move guide end to end: the merge that remap and adopt printed for a collision was refused with rc 2, since the collision's recorded folder is the live new folder. tests/test_bucket_collision_hint.py now runs the printed command."
expires:
  condition: "merge no longer refuses a source whose recorded folder exists, or the collision verbs stop printing a command"
  review_after: 2027-05-23
status: candidate
---

`remap` and `adopt` both said "fold the fresh bucket with `daimon bucket merge
--from=G --into=B`". A collision only exists when the new folder exists, and the
fresh bucket G records exactly that folder, so `merge` refused it (a live
recorded folder would birth G again). Every test checked that the hint was
printed; none ran it. The person following the guide hit a refusal with no way
forward but reading `merge`'s own message.

What to do instead: build the command in one function that both verbs call, and
assert behavior, not text: parse the printed command out of the output, run it
with `--apply`, then run the rebind it was blocking. The same applies to any
hint that names another verb (`doctor` suggestions, the unbound notice).

The command carries `--force` here on purpose, safe only because the next step
binds the folder to B. Do not widen it to a place where nothing rebinds.
