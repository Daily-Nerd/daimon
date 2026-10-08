---
id: 0
type: landmine
title: jsonl.Refused is an OSError, so a module appender's except OSError swallows a surfaced refusal unless except jsonl.Refused re-raises first
severity: high
confidence: 0.9
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/jsonl.py
  - pattern: "jsonl\.append_as\("
evidence:
  - note: "#1132 PR 10b: tests/test_cli_refusal.py::test_the_cli_run_alone_surfaces_the_refusal fails if the re-raise is removed from any appender"
expires:
  condition: "Refused stops being an OSError, or the appenders stop catching OSError"
  review_after: 2027-04-08
status: candidate
---

A refused write raises `jsonl.Refused`, an `OSError` on purpose, so every library
caller that already turns an `OSError` into "not written" (False) keeps doing so.
The CLI is the one run that must see the refusal, so `jsonl.append_as` re-raises
inside `surface_refusals()`. Every module appender wraps its write in
`try: ... except OSError: return False`, and `Refused` IS an `OSError`: without
`except jsonl.Refused: raise` placed BEFORE it, the surfaced refusal is caught
there and the verb prints "ledger unwritable" and exits 1 instead of the real
reason and exit 2. Any new appender needs the same two clauses in that order,
and a refusal must never be turned into a silent False inside the CLI.
