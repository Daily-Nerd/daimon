---
id: 0
type: fence
title: daimon_ui/reader.py's trust.jsonl fold (_active_quarantine_keys) is a hand-duplicated copy of trust.fold — a future event/channel added to trust.py does not reach it automatically
severity: high
confidence: 0.85
created: 2026-09-24
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_ui/reader.py
  - pattern: "_active_quarantine_keys"
evidence:
  - note: "#1109 PR 2 (Daily-Nerd/daimon) — daimon_ui/reader.py module
      docstring: 'No daimon imports — files are the seam', so trust.py's
      quarantine state machine could not be imported and was re-implemented"
expires:
  condition: "daimon_ui gains a supported way to call into daimon_briefing
    (a stable read-only API boundary, not a raw-file duplicate), or trust.py's
    event/channel vocabulary is frozen by its own test contract"
  review_after: 2027-03-24
status: candidate
---

`daimon_ui/reader.py` carries no import of `daimon_briefing` by design (file
docstring: "files are the seam") — it reads checkpoint JSON, events.jsonl,
verification.jsonl, and now trust.jsonl directly, and already had a
duplicate `resolutions()` fold for events.jsonl before this PR.
`_active_quarantine_keys()` follows the same pattern for trust.jsonl:
`quarantined`/`confirmed`/`dismissed`/`released` event handling,
`CHANNEL_AUTHORITY`'s human/agent split (hard-coded as
`_TRUST_HUMAN_CHANNELS`), and the "reopen a dismissed/released record"
doctrine are all copied by hand from `trust.py`'s `fold()`, not shared code.

If a future PR adds a new event to `trust.EVENTS`, a new channel to
`trust.CHANNEL_AUTHORITY`, or changes the reopen/first-writer-wins doctrine in
`trust.fold()`, this file's copy does NOT change with it — the viewer keeps
folding the ledger by the OLD rules. Depending on the direction of the drift
this either shows a quarantined item that daimon (via daimon_briefing) no
longer considers active, or (worse) fails to withhold one that IS active,
because a new event/channel this file doesn't recognize is silently ignored
by its `if event == "confirmed" and ...` / `elif` chain rather than raising.

A future editor changing `trust.py`'s event or channel vocabulary must also
update this file's copy by hand, and should add or extend a locking test
(`test_reader_content_key_stays_in_sync_with_normalize` in
`tests/ui/test_reader_quarantine.py` covers only the hash algorithm today,
not the event-fold state machine) that fails loudly on drift rather than
relying on a reviewer noticing the two files disagree.
