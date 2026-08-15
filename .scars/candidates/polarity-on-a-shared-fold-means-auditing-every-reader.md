---
kind: landmine
status: candidate
anchors:
  - path: plugin/daimon_briefing/refutations.py
    symbol: listing
violation: "refutations\\.listing\\((?!.*polarity)"
---

# Adding a polarity to a shared fold means auditing every reader, not only the one you are editing

## What happened

#693 added ruling polarity to the refutation ledger's fold and scoped the CLI
(`refute list` gained `polarity="refutation"`), but the viewer's lane at
`daimon_ui/server.py` called `refutations.listing(project_dir=slug)`
unfiltered — so a human-ratified standing ruling rendered in the shipped
viewer as `[✗ active] — Refutes: <subject>`: the exact polarity inversion the
feature was designed to prevent, on the one surface a human looks at.
`listing()`'s own docstring says the sort lives in the module "so the
viewer's lane and the CLI cannot drift apart"; the drift arrived through the
PARAMETER, not the sort. Both blind reviewers found it independently; the
viewer's test mirrored the unfiltered call and locked the bug in green.

## The rule

When a shared read surface (fold, listing, search) gains a discriminating
field, enumerate EVERY caller before shipping — `rg` the function name and
decide each call site explicitly. A caller you did not touch inherits the
widened result set silently, and a test that mirrors the call inherits the
bug. Same class as the forget-selector arc (#698): the property was verified
at the layer where it is stated, not the layer where it is used.
