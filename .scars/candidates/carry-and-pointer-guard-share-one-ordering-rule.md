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
  - pattern: "_stamp_first_seen\b"
evidence:
  - commit: aabb447
  - note: "#1236 design, section 6 'Older than the live latest': carry_forward folded the newer latest into an older import and could emit corroborations from it"
expires:
  condition: "carry reads its prev from the write's own rotation step so there is one read and one comparison"
  review_after: 2027-04-26
status: candidate
---

`store.write_checkpoint` guards each `latest` pointer with `_pointer_regresses`
(#123): a checkpoint whose `created` is older than the pointer's writes its
per-session file and leaves `latest` alone. `capture.carry_forward` runs BEFORE
that write and reads the same latest as its carry source. Until aabb447 it had
no ordering check at all, so the guard and the carry disagreed about the same
checkpoint: the guard said "this is history, keep it off latest" while carry
folded the newer latest into it, gave it items with a `first_seen` after its
own `created`, and corroborated the newer session's items from the older one.
An import of an old transcript, a heal of an old session, and the orphan sweep
catching up behind a live capture all hit it.

The fix is one predicate, `store._created_regresses`, read by the guard
(`_pointer_regresses`), by carry (`store.regresses_latest`) and by the birth
stamp (`_stamp_first_seen`, which also reads the newer latest and used to copy
its `first_seen` onto an exact-text match in the older checkpoint). Do not
re-derive the comparison in any reader, and do not add another place that
decides "is this checkpoint older than latest" (a door flag, a cli switch, a
carry option): a second copy of the rule is how the readers drifted in the
first place. Every doubt (no prior, no `created` on either side) answers False
everywhere, so last-write-wins and carry-as-before stay the default; only a
provable regression is blocked.

The decision is not one atomic read: carry asks before the write, the guard
asks inside the pointer lock. A newer write landing between the two can only
make carry fold an older prev forward, which is the ordinary direction, so the
readers can disagree only harmlessly. Folding the carry read into the write's
own rotation step would close even that, and is the condition that retires
this scar.
