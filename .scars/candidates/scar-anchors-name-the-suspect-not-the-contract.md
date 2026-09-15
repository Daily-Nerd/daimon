---
id: 0                      # assigned at promotion (next free NNNN)
type: landmine             # deadend = tried+failed | fence = looks wrong, intentional | landmine = touching A breaks B
title: A scar anchored to the module it SUSPECTS never fires on the module that actually breaks the contract
severity: high
confidence: 0.9
created: 2026-09-15
authors: ["claude-code"]
anchors:
  - path: .scars/
  - path: plugin/daimon_briefing/briefing.py
evidence:
  - issue: 1034
  - note: ".scars/0006-briefing-cap-assumes-chronological-decisions.landmine.md"
expires:
  condition: "scar liveness gains a data-contract anchor form (anchor the invariant's consumers AND every producer, not a named file list)"
  review_after: 2027-03-15
status: candidate
---

Scar 0006 described a real invariant (the briefing decision cap slices the tail,
so `recent_decisions` must run oldest to newest) and then anchored the two files
its author SUSPECTED would break it: `briefing.py`, which consumes the order, and
`serializer.py`, which first writes the list. The module that actually broke it
was `carry.py`, which appends older items at the tail of that same list long
after the serializer is done. The scar was correct, active, high confidence, and
silent, because the pre-edit hook only fires on anchored paths. A prediction that
names the wrong suspect is indistinguishable from no prediction at all.

When you write a scar about a DATA CONTRACT rather than about one function's
internals, anchor every module that can write, append to, sort or reorder that
data, not just the one you are editing today. If you cannot enumerate them, say
so in the body and anchor the directory. Checking this cost about a minute here:
`rg` for the field name across the package finds the producers that the original
author could not have known about.

Related trap met in the same change: `carried_from` is stamped ONLY on carry's
plain-carry branch, never on a twin (reworded restatement) pair, which is why
"native" can be defined as "no `carried_from`" without misclassifying a twin.
See fence 0077. Do not switch the native test to some other field without
re-reading that one.
