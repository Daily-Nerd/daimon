---
id: 0
type: fence
title: "A refused-admission line puts (session: id) BEFORE (transcript: path) because the transcript regex swallows whatever follows the path"
severity: medium
confidence: 0.9
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/ledger.py
  - pattern: "_ERR_SESSION_RE"
evidence:
  - note: "#1132 PR 10b: tests/test_admission_ledger.py::test_the_session_group_before_the_transcript_group_leaves_the_path_whole"
expires:
  condition: "the transcript regex stops accepting a trailing group"
  review_after: 2027-04-08
status: candidate
---

The result line `error: ... (transcript: <path>) after Ns` is parsed by
`_HEAL_TRANSCRIPT_RE`, whose path group is `(.+?)` followed by `\)( after Ns|$)`.
Anything placed after the transcript group would be folded into the path and heal
would look for a file that does not exist. The session group therefore sits
before it, and both log folds (the package and the stdlib-only hook library)
read it with their own regex. It is printed only when the serialize was given
`--session`, which is what keys a Kimi refusal (transcript stem always `wire`) to
the real session. Do not "tidy" the order, and change both folds together.
