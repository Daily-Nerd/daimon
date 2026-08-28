---
id: 56
type: landmine
title: A per-bucket requests fold reads your OUTGOING asks, never your inbox — an ask is stored in the sender's bucket
severity: high
confidence: 0.95
created: 2026-08-28
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/pending.py
  - pattern: "requests\.records\("
evidence:
  - commit: 8cdbede
  - note: daimon decide shipped listing 4 outgoing asks and omitting 2 asks addressed to this project, verified against real ledgers 2026-08-28
  - note: requests.py:472 records() == fold(events(project_dir)) reads ONE file; requests.py:845 recipient_join() is the cross-bucket join and drops outgoing at :872-874
expires:
  condition: "requests grow a recipient-side index, or open_request dual-writes an addressed-to row into the recipient's bucket, so a per-bucket fold can see an inbox"
  review_after: 2027-02-28
status: active
---

`requests.records(project_dir=X)` is `fold(events(X))` (requests.py:472): it reads exactly
one file, `checkpoints/X/requests.jsonl`. That file holds the `opened` rows X WROTE, which
are the asks X SENT to other projects. It cannot contain an ask addressed TO X, because
`open_request` writes the row into the SENDER's bucket.

So `records()` reads like "this project's requests" and is in fact "this project's OUTBOX".
The two readings differ by 180 degrees and nothing in the name or signature says which one
you get. `daimon decide` shipped on this mistake (#766 slice 1, commit 8cdbede) and looked
correct in tests, because a test that opens a request locally and reads it back locally is
exercising a self-addressed ask, the one case where both readings agree.

If you want asks addressed to this project, call `requests.recipient_join(project_dir=)`
(requests.py:845). It joins across every bucket holding a requests ledger and already
excludes this project's own outgoing asks (:872-874). `requests.inbox_listing` (:894) is
the shipped consumer to copy, including its `state not in _SENDER_MOVABLE` filter.

Two consequences that bite downstream. First, any "local only, no cross-bucket read" scope
claim is unavailable for an inbox surface: the data is not where it is addressed, so the
join is not a cost you can defer behind a flag. Second, ordering that breaks ties on append
position must take that position from the ORIGIN bucket's event order (the record's
`from_slug`), because a foreign ask has no index in the local event list and silently
falls back to 0.

This is not scar 0055's cross-bucket render problem. An ask addressed to you is your own
mail. What 0055 still forbids is another project's OWN record text.
