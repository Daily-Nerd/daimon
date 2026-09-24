---
id: 90
type: landmine
title: "A gate added to requests._stamp does NOT protect accept's agent path, which writes a pre-stamped row"
severity: high
confidence: 0.9
created: 2026-09-12
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - pattern: "_write_verdict_row\("
evidence:
  - commit: d65fbf6
  - pr: 1028
  - note: #1026: the accept gate was mutation-tested. Removing it made an agent accept carrying author=\"alice\" land a row with no act_author, exit code 0, no refusal.
  - note: "firing-review 2026-09-24: the old evidence commit 71890b7 was a pre-squash SHA, unreachable from HEAD, and `scar lint` flagged it evidence-unreachable. Replaced with d65fbf6 (PR #1028, 'feat(requests): accept a per-act author on the in-process ui writer path', closes #1026), verified an ancestor of HEAD via `git merge-base --is-ancestor`."
expires:
  condition: "_write_verdict_row stops accepting a caller-built `row`, or the dry-run in accept is re-expressed so the row it writes is stamped by _stamp"
  review_after: 2027-03-12
status: active
---

`requests._stamp` looks like the one chokepoint every row on this module's
write path goes through, and for `open_request`, `revise`, `done`,
`suppress` and `_verdict` it is. It is not for `accept`.

`accept`'s agent ruling-coverage branch (#961 slice 4 review round 2, H2)
stamps a SYNTHETIC row, dry-runs the fold's own coverage predicate against
that exact row's `order`, and then hands the SAME already-stamped row to
`_write_verdict_row(row=synthetic)`. That parameter exists precisely so the
row that lands is not re-stamped, which means `_stamp` is never called on
that path at all. Any validation you add to `_stamp` is silently absent
there.

#1026 hit this adding a per-act `author` keyword. With the gate only in
`_stamp`, `accept(channel="cli-agent", author="alice")` did not refuse: it
took the agent branch, never reached `_stamp`, and appended a verdict row
that dropped the name and recorded the process identity instead. No error,
no log line, and the fold happily rendered a verdict attributed to the
wrong identity. The fix is a second explicit call at the top of `accept`.

Before adding any check to `_stamp`, ask whether it must also hold for a
caller-supplied `row`, and if so put it in `accept` too. A green suite will
not tell you: the agent path has its own tests, and none of them pass the
argument your new check inspects.
