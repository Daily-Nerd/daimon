---
id: 0
type: landmine
title: A helper that spawns a process per call is safe only while it has one caller per process, and author() stopped having one
severity: medium
confidence: 0.8
created: 2026-10-17
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/config.py
  - pattern: 'subprocess\.run\(\s*\[\s*"git"'
evidence:
  - issue: 1203
  - commit: 63c99f5
expires:
  condition: "author() stops being called per stamped row, or the git user name moves out of the process-level memo"
  review_after: 2027-04-17
status: candidate
---

`config.author()` once ran `git config user.name` on every call, and that was fine
because only the session-end write called it. Ledger stamps then grew callers:
`refutations._stamp`, requests, trust, amendments and relations all put
`author()` on every row. The suite paid 14954 spawns of about 24 ms each, 303 s of 1253 s.

The fix memoizes the lookup per process per working directory (`_git_user_name_in`).
Failures are memoized on purpose: one process stamps one identity and never flips
mid-run. The memo MUST be cleared in the autouse fixture in `tests/conftest.py`,
or a test that patches git or chdirs leaks its identity into later tests.
Before adding a caller to any helper that forks, check how often it will run.
Do not memoize `git_branch`: a branch really changes, and it runs once per write.
