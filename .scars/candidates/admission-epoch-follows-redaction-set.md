---
id: 0
type: landmine
title: Editing the redaction patterns, the peer message grammar or signed-act verification without bumping ADMISSION_EPOCH strands held messages
severity: medium
confidence: 0.7
created: 2026-11-15
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/peer_messages.py
  - path: plugin/daimon_briefing/redact.py
evidence:
  - note: "#1350, plugin/tests/fixtures/peer_messages_vectors.json pins the epoch"
expires:
  condition: "a derived epoch (a digest of the redaction set and the grammar) replaces the hand-set constant"
  review_after: 2027-05-15
status: candidate
---

`peer_messages.admit` holds a message it cannot take in custody (a prose field
that matches a redaction rule, a body from a newer grammar, a signed act that
awaits verification), and the transport keeps it and offers it again only when
`ADMISSION_EPOCH` changes. The epoch is a hand-set string, so nothing links it
to the three things that decide a hold: `redact._PATTERNS`, the grammar in
`peer_messages`, and whether signed-act verification exists.

Change any of those and leave the epoch alone, and a message held by the old
rules is never offered again: the sender sees it as undelivered until the
transport's retention ends. The reverse also bites: a new pattern that makes
a previously clean body a secret does nothing for messages already acknowledged.

When you change one of the three, bump `ADMISSION_EPOCH` in the same commit and
update the epoch in the vectors file, which a test compares to the constant.
