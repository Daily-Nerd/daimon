---
id: 0
type: landmine
title: identity.claim must not raise OSError and BucketMismatch must not subclass it, or every ledger writer changes contract
severity: high
confidence: 0.8
created: 2026-11-22
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/identity.py
  - path: plugin/daimon_briefing/store.py
  - pattern: "identity\.claim\("
evidence:
  - commit: 17b44b9
expires:
  condition: "the ledger writers stop treating OSError as a best-effort False, or claim moves inside each writer's own try block"
  review_after: 2027-05-22
status: candidate
---

Every ledger writer (append, append_events, write_checkpoint and the rest) ends
in `except OSError: return False` around its `ensure_bucket`. Slice 4a moved the
bucket birth to a `claim` at the TOP of each writer, outside that handler, so a
`mkdir` failure raised there would turn "unwritable store, returns False" into
an exception out of the writer.

So `claim` swallows nothing and raises nothing for the directory: if the
`mkdir` fails it returns the ref and the writer's own `ensure_bucket(ref)`
retries the same `mkdir` inside the handler it already had. Making claim raise
`OSError` changes the contract of eleven writers at once.

The opposite holds for `BucketMismatch`: it is deliberately not an `OSError`
subclass, because those same handlers would absorb it into a quiet `False`. Do
not "tidy" the hierarchy. Handlers that carry on after any `Exception` name
`identity.LOUD` first and re-raise; tests/test_bucket_owner_census.py fails on
a new broad except around a claimer that does not.
