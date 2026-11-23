---
id: 0
type: landmine
title: writer=Writer.HUMAN on an events write is ledger posture, not a trust tier; the tier is the row's source
severity: medium
confidence: 0.85
created: 2026-11-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/handoff.py
  - path: plugin/daimon_briefing/handoff.py
  - path: plugin/daimon_briefing/render.py
evidence:
  - commit: c513b02
  - note: "daimon handoff passed writer=Writer.HUMAN on every write while the row's source was already observed (cli-tty or agent); the brief ignored the source and printed one fixed 'left deliberately by previous session' label, so an agent's baton read as a person's. The in-process agent writer passes the same Writer.HUMAN and an agent source."
expires:
  condition: "Writer is renamed so its HUMAN member no longer reads as an authority claim, or a verb stops using it for agent writes"
  review_after: 2027-05-23
status: candidate
---

`Writer` (surfaces.py) picks the ledger-health posture of a write: whether it
proceeds, refuses or skips on a damaged ledger. `Writer.HUMAN` means "a verb
ran", and agent-tier verbs use it too (`request` agent claims, `resolve --by
agent`, `api.write_handoff`). It says nothing about who authored the row.

Who authored an events row is its `source`, read through
`authority.of_row("events", row)`. A renderer or a gate that wants to know
whether a person wrote something reads that, never the writer. The baton header
reads it, and a source no table knows reads as not human.

Do not "fix" the writer argument of an agent write to something else to change
its tier, and do not infer a tier from it. Change the `source` the caller
passes, and keep that decision in `cli/_observe.py` and `authority.py`.
