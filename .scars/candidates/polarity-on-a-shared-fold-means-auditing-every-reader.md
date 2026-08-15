---
id: 0
type: landmine
title: Adding a discriminating field to a shared read surface silently widens every caller you did not audit
severity: high
confidence: 0.9
created: 2026-08-15
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/refutations.py
  - path: plugin/daimon_ui/server.py
violation: "refutations\.listing\(\s*project_dir"
evidence:
  - note: "issue #693 PR1 review: two blind reviewers independently executed the repro"
expires:
  condition: "refutations.listing grows a required polarity parameter, making unscoped calls impossible"
  review_after: 2027-02-15
---

#693 added ruling polarity to the refutation ledger fold and scoped the CLI
(`refute list` passes `polarity="refutation"`), but the viewer lane called
`refutations.listing(project_dir=slug)` unfiltered — so a human-ratified
standing ruling rendered in the shipped viewer as an active refutation of its
own subject: the exact polarity inversion the feature was designed to
prevent, on the surface a human actually looks at. The viewer's test mirrored
the unfiltered call, so the suite locked the bug in green. When a shared read
surface (fold, listing, search) gains a discriminating field, rg the function
name and decide EVERY call site explicitly before shipping; a caller you did
not touch inherits the widened result set silently, and any call omitting the
polarity argument (the violation regex above) is the bug shape recurring.
