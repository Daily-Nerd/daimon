---
id: 0
type: fence
title: daimon diff has no forgotten change class on purpose, so a restated item whose old wording was forgotten reads as added
severity: medium
confidence: 0.8
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/history.py
  - pattern: "_CHANGE_ORDER"
evidence:
  - note: "#1132 PR 11a: tests/test_history_diff.py::test_there_is_no_forgotten_change_class and ::test_a_real_forget_leaves_nothing_in_either_body_or_the_listing"
expires:
  condition: "a person rules that a listing may announce a forgotten id"
  review_after: 2027-04-09
status: candidate
---

A listing of two generations names, counts and lists no forgotten value. The
exact-id verbs (`why`, `blame`, the viewer's why page) may say
`[withheld: forgotten]` for the one id they were asked about, because the id
oracle equals the tombstone key set a teammate already receives. A listing is
different: a `forgotten` row would announce, in bulk, every id a person ever
erased, and a count would confirm a guess about how much.

So `diff` drops the class. After a real forget the item is in neither body and
has no row. A residue copy withheld by the machine-wide forget set is treated
as absent on both sides, which means an item restated from a forgotten wording
shows only its new wording, as `added`. That looks like a missing `restated`
row and is not one. Quarantine and an unreadable trust ledger do get a
`withheld` row (identity and generation, never the text), because a person has
a record to act on for those.
