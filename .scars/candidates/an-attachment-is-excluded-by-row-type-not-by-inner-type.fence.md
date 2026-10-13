---
id: 0
type: fence
title: Attachment rows are dropped by top-level type, never by an allow-list of inner types
severity: medium
confidence: 0.8
created: 2026-10-12
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/transcript.py
  - pattern: 'get\("type"\) == "attachment"'
evidence:
  - commit: 1373190
expires:
  condition: "a host writes conversation text into an attachment row that a capture must read"
  review_after: 2027-04-12
status: candidate
---

Claude Code writes 34 inner attachment types (hook output, reminders,
environment, queued commands) and adds more over time. Hook output, daimon's
own SessionStart briefing among it, arrives as attachment rows, so admitting
any of them risks the capture reading its own output back. The rule is the
row type, even when the row also carries a `message` dict, because
`_role_of` would otherwise give that row a role and admit it.

Do not turn this into a list of inner types to drop. The next new type would
be admitted by default. If a host really needs one attachment kind, admit it
by name in `excluded_reason` with a test, and update api.EXCLUDED_ROWS.
