---
id: 75
type: fence
title: "`daimon ruling list` still reads the ledger twice: `rulings_read` only settles no-bucket/unreadable, `listing()` still does the actual row fetch"
severity: low
confidence: 0.8
created: 2026-09-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/cli/ruling.py
  - path: plugin/daimon_briefing/briefing.py
  - pattern: "briefing\.rulings_read"
evidence:
  - note: #962: briefing.rulings_read(project_dir) reads standing rulings only (state==\"active\", polarity==\"ruling\"), but `daimon ruling list` supports --state candidate/overturned and is a general listing, so `_cmd_ruling_list` still has to call refutations.listing() separately for `rows`. It calls rulings_read() a second time, only when rows is empty, purely to learn which of the four states (unresolved/no-bucket/unreadable/read) applies.
expires:
  condition: "listing()/records() gain their own strict= parameter so one call produces both the rows and the read-state, closing the second read"
  review_after: 2027-03-01
status: active
---

`rulings_read` is scoped to active rulings only, by design (#962's own contract:
it is the sibling of `active_rulings`). `daimon ruling list` is not scoped that
way — it lists candidate/active/overturned rulings via `--state`, and always
polarity `"ruling"`. Those two shapes cannot be satisfied by a single read, so
`_cmd_ruling_list` keeps its original `refutations.listing(...)` call for the
rows it renders, and *separately* calls `briefing.rulings_read(project)` (only
when `rows` comes back empty) purely to classify the empty answer as one of
`rulings_read`'s four states: `"unresolved"`, `"no-bucket"`, `"unreadable"`,
or a clean `"read"`.

This closed the correctness gap #962 was filed for (the CLI no longer reports
"cannot read the ledger" as "no rulings"), but it did not close the TOCTOU the
task's own design note flagged: two independent reads of the same file, a
handful of instructions apart. In practice the window is a few microseconds
and `listing()`'s own fail-open swallow means a race can only ever make the
report LESS specific (a transient unreadable moment reported as a clean empty
read), never wrongly alarming — so this is a fence, not a landmine: the
remaining gap looks like duplicate work, but leaving it is intentional given
the scope decided for #962 (widen `events()` only). Closing it for real needs
`listing()`/`records()` to grow the same `strict=` parameter `events()` got, so
`_cmd_ruling_list` can do one call and derive both facts from it.
