---
id: 0
type: landmine
title: write_checkpoint stamps project_slug with setdefault, so a reused dict keeps the FIRST project's stamp
severity: medium
confidence: 0.95
created: 2026-08-28
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/receipts.py
  - pattern: "setdefault.*project_slug"
evidence:
  - note: "#791 — read_latest_reportable decides membership by the payload stamp"
expires:
  condition: "write_checkpoint re-stamps project_slug from project_dir on every write"
  review_after: 2027-02-28
status: candidate
---

`write_checkpoint` MUTATES the dict it is handed, in place, and stamps identity
fields with `setdefault` (`store.py:1142-1190`): `project_slug`, `project_name`,
`author`, `created`, `format_version`, `git_branch`. The idempotence is
deliberate, because some callers stamp before calling (#123), and team fan-in
decides membership by the payload's own `project_slug` rather than by which
directory the file sits in (`store.py:274`, `store.py:291`).

The edge: `setdefault` does NOT re-stamp. Write a checkpoint dict to project A,
then hand the SAME dict to `write_checkpoint(..., project_dir=B)`, and the file
lands in B's bucket still claiming A's slug. Verified: the second write leaves
`project_slug` unchanged.

That matters more since #791, because `store.read_latest_reportable` refuses a
checkpoint whose stamped slug names a different project. A checkpoint carrying a
stale stamp is therefore foreign to the very bucket it lives in, and the surfaces
that use it will report "no checkpoint for this project yet" while a file sits
right there. Team fan-in reads it the same way.

Two rules. Never reuse a checkpoint dict across projects: re-read it, or delete
the identity keys before the second write. And when a probe or test builds a
payload by copying one that has already been written, it is carrying a stamp it
did not ask for. That is a real failure mode and it produced a false negative in
a manual check before it was understood: the code was correct and the probe was
not.
