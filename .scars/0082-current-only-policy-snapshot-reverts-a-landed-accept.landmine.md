---
id: 82
type: landmine
title: Injecting a CURRENT-STATE ruling snapshot into a fold's re-check of an already-landed decision makes that decision depend on another ledger's later state
severity: high
confidence: 0.9
created: 2026-09-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/requests.py
  - path: plugin/daimon_briefing/refutations.py
  - pattern: "active_request_policies|request_policy_history"
evidence:
  - note: #961 slice 4 build, 2026-09-09: the first implementation injected refutations.active_request_policies (state == active only) into requests.fold's widened accept exception. Two tests failed identically: a ruling retired AFTER an agent accept landed under it, and a ruling revised to a different sender AFTER the same, both reverted the already-accepted record back to open on the very next re-fold, because fold recomputes state from scratch every call from a policy set resolved fresh each time. Fixed by adding refutations.request_policy_history, a MONOTONIC set built by folding every prefix of the ruling ledger and accumulating every snapshot where the grant was active, so a hash that was ever legitimately active stays valid for the fold's re-check forever, even past the ruling's later overturn. The write boundary (accept()) correctly kept using the current-state snapshot, since refusing NEW accepts after overturn is a different question the fold's own re-check must not answer the same way.
expires:
  condition: "requests.fold or refutations.active_request_policies changes shape enough that this reasoning no longer applies, or the fold gains genuine memory across calls"
  review_after: 2027-03-09
status: active
---

Whenever a fold-style function re-derives a record's state from scratch on
every call, and that derivation consults a SECOND ledger's CURRENT state to
decide whether an already-landed transition still counts, a later change to
the second ledger silently reverts the first. The bug reads as "the accept
disappeared" with no error anywhere: `fold` ran cleanly, the policy set was
resolved cleanly, the membership test just came back false the second time.

The fix is not "cache the old result" (the fold has no state to cache into)
but to make the injected fact itself monotonic: a set of everything the
second ledger ever validated, not everything it validates right now. The
write boundary that MINTS a new dependent fact should still use the
current-state question; only the re-derivation of a past one needs the
historical question. Conflating the two in one resolver function is the
trap — build two, name them for the question each answers, and never let a
caller reach for the wrong one by habit.
