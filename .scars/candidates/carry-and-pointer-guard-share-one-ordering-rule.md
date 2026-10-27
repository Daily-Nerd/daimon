---
id: 0
type: landmine
title: carry_forward and the pointer guard must read the same ordering rule, or an older capture carries the future
severity: high
confidence: 0.9
created: 2026-10-26
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/capture.py
  - pattern: "_pointer_regresses\b"
  - pattern: "regresses_latest\b"
evidence:
  - commit: fe64094
  - note: "#1236 design, section 6 'Older than the live latest': carry_forward folded the newer latest into an older import and could emit corroborations from it"
expires:
  condition: "carry reads its prev from the write's own rotation step so there is one read and one comparison"
  review_after: 2027-04-26
status: candidate
---

`store.write_checkpoint` guards each `latest` pointer with `_pointer_regresses`
(#123): a checkpoint whose `created` is older than the pointer's writes its
per-session file and leaves `latest` alone. `capture.carry_forward` runs BEFORE
that write and reads the same latest as its carry source. Until fe64094 it had
no ordering check at all, so the guard and the carry disagreed about the same
checkpoint: the guard said "this is history, keep it off latest" while carry
folded the newer latest into it, gave it items with a `first_seen` after its
own `created`, and corroborated the newer session's items from the older one.
An import of an old transcript, a heal of an old session, and the orphan sweep
catching up behind a live capture all hit it.

The fix is one predicate, `store._created_regresses`, read by the guard
(`_pointer_regresses`) and by carry (`store.regresses_latest`). Do not re-derive
the comparison in either reader, and do not add a third place that decides
"is this checkpoint older than latest" (a door flag, a cli switch, a carry
option): a second copy of the rule is how the two drifted in the first place.
Every doubt (no prior, no `created` on either side) answers False in both, so
last-write-wins and carry-as-before stay the default; only a provable
regression is blocked on both sides.
