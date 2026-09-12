---
id: 0
type: landmine
title: carried_from freezes at the FIRST hop, not the last, because carry copies it under setdefault
severity: medium
confidence: 0.9
created: 2026-09-12
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/carry.py
  - path: plugin/daimon_briefing/cli/history.py
evidence:
  - note: "Measured 2026-09-12 while building `daimon blame` (#975). Three sessions S-1/S-2/S-3, each folding the previous with carry.merge. The item first stated in S-1 renders `carried from S-1` in BOTH prev-1.json (S-2) and latest.json (S-3). If the field named the last hop, latest.json would say S-2."
expires:
  condition: "carry stops copying carried_from under setdefault, or a separate last-hop field is added"
  review_after: 2027-03-12
status: candidate
---

Two docstrings say `carried_from` "names the LAST hop": carry.py's
`_record_corroboration` (G1) and policy.py's `bind_origin`. The code does not
do that. carry's plain-carry branch deep-copies the previous item and then
calls `kept.setdefault("carried_from", prev_sid)`, so a value already on the
copy survives untouched. The field is written exactly once, on an item's FIRST
carry, and every later generation inherits that same session id.

This matters to anything reconstructing a chain. `carried_from` answers "which
session did this item first get copied out of", never "which checkpoint did
this generation read as prev". A reader that walks the pointer chain and
assumes each generation's label names its immediate predecessor will draw a
lineage that is wrong from the second hop onward, and it will look right,
because the id it prints is a real session that really did hold the item.

The docstrings are not wrong about the conclusion they draw. Both are arguing
that `carried_from` cannot answer "who first wrote this", and they are right
for a stronger reason than the one they give: it names the first CARRIER, and
on the twin path it is not stamped at all (scar 0077). `origin_session`, bound
at the write boundary, is still the only field that answers authorship.

`daimon blame` (#975) renders the field as a copy label and says nothing about
which generation it came from, which is why it reads correctly. Keep that
framing if the rendering is ever reworked, or add a genuine last-hop field
rather than re-reading this one as if it were one.
