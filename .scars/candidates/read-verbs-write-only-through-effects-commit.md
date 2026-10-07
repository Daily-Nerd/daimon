---
id: 0
type: fence
title: A read verb records usage, stamps and ledger rows only through effects_commit, after its output is flushed
severity: high
confidence: 0.8
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/effects_commit.py
  - path: plugin/daimon_briefing/mcp_tools.py
evidence:
  - note: "tests/test_effects_commit.py pins the order per host family (CLI brief, loops, status, projects, every MCP handler)"
expires:
  condition: "the read verbs stop recording effects, or effects_commit is replaced by another single commit point"
  review_after: 2027-04-07
status: candidate
---

A briefing host builds its output, writes it, and only then commits what it
decided to record: the usage line, the worldcheck ledger rows, the `surfaced`
stamps, the recall telemetry row. `effects_commit.commit` flushes stdout before
its first write, so a card that was printed is the card that is stamped, and a
crash between the print and the stamp shows the card again instead of
recording it as seen.

Usage is the exception to "success only": hosts commit in a `finally`, so the
line is written when the verb exits 1 or 2 or an MCP handler raises a
ToolError (every attempt counts in `daimon stats`). Stamps and worldcheck rows
are added to the pending effects only after the output was written.

Do not call `_note_usage`, `requests.stamp_surfaced` or a ledger writer from a
read verb body, and do not add a second stamp writer: the stamps go through
`requests.stamp_surfaced` / `stamp_verdict_surfaced`, the `_stamp` chokepoint.
