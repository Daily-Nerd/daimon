---
id: 98
type: fence
title: "#1087 skipped the design's found-group row order — it fights pending.py's own oldest-first backlog doctrine"
severity: low
confidence: 0.7
created: 2026-09-22
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/pending.py
  - pattern: "_order_key"
evidence:
  - note: vault decisions/2026-09-22-amendment-confirm-fatigue-design.md, Design section 1 (\"Order: user turn, then tool output, then the ⚠ groups. Oldest first within each group.\") vs pending.py:_order_key's own comment (\"a deliberate inversion... this is a backlog, and the oldest undecided item is the one rotting\")
expires:
  condition: "a future change explicitly reorders the amendment lane by found-group and adds a test for it"
  review_after: 2027-03-22
status: active
---

The #1087 design asks the amendment lane's rows to sort by WHERE the quote
was found — user turn, then tool output, then the flagged (⚠) groups —
oldest first only within each group. `pending._order_key` sorts the WHOLE
merged decide queue (every lane) by `(not blocking, waiting_since, seq,
kind_rank, id)`, and its own comment says that ordering is deliberate: "the
oldest undecided item is the one rotting." Grouping amendment rows by
found-role would mean a same-day tool-output row jumps ahead of a
three-week-old assistant-flagged one, which contradicts that doctrine for
this one lane only.

The design's own Tests section (the authoritative list for what #1087 must
ship) does not include an ordering test, so this was left unimplemented
rather than guessed at. If a future editor wants the found-group order, it
has to answer: does it apply only within same-`waiting_since` ties (safe,
low-value), or does it override chronological order for amendment rows
specifically (a real doctrine change that needs its own decision and test,
not a silent tuck-in in a rendering pass)? Do not "fix" this by sorting
`_amendment_rows`' output before it reaches `_order_key` without checking
which reading was intended — the two readings produce very different
backlogs.
