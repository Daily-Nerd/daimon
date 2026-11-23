---
id: 0
type: fence
title: The resolver tries the recorded roots BEFORE an unstamped seed directory, and tenant scope and SEALED never scan
severity: high
confidence: 0.85
created: 2026-11-22
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/identity.py
  - pattern: "_index\(\)"
evidence:
  - commit: ee0a99e
  - note: "Slice 4 refutation rev 3, N3: an unstamped directory at the seed name captured a location forever, even after adopt had bound another bucket to it"
expires:
  condition: "the resolver is rewritten around a derived index that answers both orders the same way"
  review_after: 2027-05-22
status: candidate
---

`identity.resolve` runs: the declared slug (SEALED), the seed's own root, ONE
bucket anywhere whose root names the location (the shared index), an unstamped
seed directory, then the first free birth name. Moving the unstamped-directory
step ahead of the scan looks like a cheap win (it saves the scan for the 29 of
61 legacy buckets) and breaks rebinding: a stray directory at the seed name
captures the location, and the bucket `adopt` bound to it is never found.

Under tenant scope and SEALED the scan is skipped outright, not filtered. The
bucket directory holds other tenants' roots, and a planted bucket named
anything whose root equals this tenant's path must not be returned
(tests/test_identity_resolver.py plants one). Two buckets recording one root
count as none: `bucket doctor` names both, the resolver guesses neither.

The index lives only inside one `identity.operation()` and is dropped by the
next one, so `adopt`, `remap` and `merge` take effect immediately; a blocking
server verb must not run inside one for its whole life
(`long_running=True` on `serve` and `mcp serve`). A miss is never remembered.
