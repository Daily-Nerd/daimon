---
id: 84
type: landmine
title: Two independent pre-passes deriving related facts from the same founder row will disagree the moment only one applies the read-boundary shape check
severity: high
confidence: 0.9
created: 2026-09-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - pattern: "_founder_by_id|_founder_kind_by_id|_founder_origin_by_id"
evidence:
  - note: #961 slice 4 review round 2, item C1, 2026-09-09: slice 4 added `_founder_origin_by_id` beside the existing `_founder_kind_by_id`, both walking the same sorted `opened` rows to answer two DIFFERENT questions about the same founder row (its kind, its origin bucket). `_founder_kind_by_id` applied the read-boundary shape check (`_SLUG_RE` on `to`, a non-empty `ask`) before accepting a row as founder — the identical check `fold`'s own founder branch makes. `_founder_origin_by_id` was written fresh and never carried that check over, because origin felt like a different, simpler question. A shape-invalid `opened` row (empty ask) planted in a foreign bucket at an EARLIER order than the genuine founder therefore could not win `kind` (shape-checked pass skipped it) but DID win `origin` (unchecked pass accepted it), so a record's `kind`/`ask`/`to` came from the genuine sender while its `from_slug` — and every policy match keyed on it — came from an entirely different, invalid row a stranger controlled. Fixed by merging both passes into `_founder_by_id`, which resolves `(kind, origin_slug)` from the SAME founder row under the SAME shape check, so the two fields cannot drift apart by construction.
expires:
  condition: "the founder-resolution pattern (a pre-pass over opened rows deciding a fold-time fact before the main pass runs) is retired or replaced with a different mechanism entirely"
  review_after: 2027-03-09
status: active
---

When a fold-style reader needs two or more facts about "the row that founded
this record" (kind, origin, author, whatever), resolve them all from ONE
pre-pass over ONE sorted list, applying the read-boundary shape check ONCE,
before returning any of them. Do not write a second pre-pass "for the other
field" even when it looks like a simpler, independent question — it will
silently diverge from the first the moment a malformed or forged row can
satisfy one check and not the other, and the two answers will disagree about
which row is authoritative with no error anywhere. The fold's own founder
branch is the one true shape check; every pre-pass computing a founder-derived
fact must call the identical guard, ideally by construction (one function,
one return of a tuple/dict) rather than by remembering to copy it.
