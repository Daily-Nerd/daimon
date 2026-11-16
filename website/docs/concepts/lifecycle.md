---
sidebar_position: 4
description: "How a briefing item is born, carried, closed, revived or removed, and why daimon's three lifecycle commands never guess a transition on your behalf."
---

# The item lifecycle

A briefing item is not a static row — it moves through a lifecycle: it is
born in a session, carried while open, and eventually closed, revived, or
removed. Three commands drive the transitions, and all three share one
contract: **nothing is ever guessed on your behalf.**

## The never-guess contract

`resolve` and `forget` accept either an exact item id (`o-3f8a2c`) or a
free-text query — but the query must match **exactly one** item. An ambiguous
match is refused with the candidates listed; you pick one by id. Both commands
take `--dry-run`, which runs the same match and prints what *would* happen
without writing anything — look before the write.

One caveat the contract cannot cover: a confident match on the *wrong* item is
not ambiguous, so the refusal never fires. That is what `--dry-run` is for.

## `daimon forget` — remove, provably

Resolve closes an item but keeps its content in history. Forget is for the
cases where the content itself must go — a name that should never have been
captured, a project detail, a wrong belief that keeps carrying. Capture-time
redaction is the first line of defense; forget is the second, for judgment
calls no redaction pattern can know about.

What happens on forget:

- The item is removed from the live checkpoint, which is rewritten through
  the normal store path — redaction re-runs and, with receipts on, the
  **receipt re-mints over the post-removal bytes** (see
  [receipts](./receipts.md)).
- An append-only **tombstone event** records `forgotten:<12-char content
  hash>` — the hash, never the text. Removal means the content leaves the
  audit trail too; the trail can still prove *that* something was removed,
  when, and why (`--reason`, redacted like any note). It is written together
  with a **key row** on a ref of its own and a row for every sibling id that
  held the same value, in one append. A later `reopen` on the id replaces only
  the id row, so the value stays withheld; `daimon unforget <id>` is what lifts
  a forget (see below).
- The recall index deletes the item's rows across **all** historical
  checkpoint copies in your local index — including your local copies of team
  mirrors — so recall cannot resurrect it. (Propagating tombstones into
  teammates' own mirrors is a deliberate follow-up, not in v1.)
- The serializer's **chunk cache** is purged **wholesale**. That cache keeps
  pre-redaction extraction output for a few days so an interrupted capture
  never re-pays its LLM calls — and because its entries are keyed by chunk
  text, not searchable by contained value, selective removal is impossible.
  Forget clears all of it (the cost: chunks younger than the rotation window,
  `chunk_cache_days`, default 3 days, get re-extracted next time). Entries
  are also age-reaped at that window independently of forget. The purge is
  never fatal (removal of the belief state always completes), and a purge
  that could not finish is listed as `unreached` and makes the command exit 4.
- Briefing withhold, carry suppression, and `daimon stats` all inherit the
  tombstone through the same event stream.
- Every step above is a row of the surface registry, run in phase order, and
  each step reports whether it reached its surface. A surface forget could not
  reach is named with its cure and the exit code is 4. `daimon forget --resume`
  finishes every phase for every standing forget once the cause is fixed.

### Deletion-durability compliance

The claim "a forgotten memory stays forgotten" is not asserted — it is
committed as an executable compliance test that runs on every commit
([source](https://github.com/Daily-Nerd/daimon/blob/main/plugin/tests/test_deletion_durability_protocol.py)).
It runs a forgotten value through every path that could quietly resurrect it and
proves it stays gone at each one, while a never-forgotten twin stays
retrievable so no check can pass vacuously:

| # | Step | Result |
|---|------|--------|
| 1 | Write a distinctive fact through the serializer | retrievable |
| 2 | `forget` it — briefing, carry, recall | removed |
| 3 | Re-feed the **original source transcript** and re-serialize | not resurrected |
| 4 | Recall index rebuild | absent |
| 5 | A subsequent carry | absent |
| 6 | Team dual-write mirror | absent from the remote copy |
| 7 | Rendered brief string | absent |
| 8 | Recall SQLite rows | absent |
| 9 | Signed receipt | binds the post-deletion bytes |
| 10 | Audit trail | records the deletion, holds none of its text |
| 11 | Serializer chunk cache (pre-redaction) | purged wholesale on forget |

**Result: 11 / 11 steps compliant.** The tests are deterministic and use zero
model quota — a canned extractor and a stubbed signer stand in for the LLM and
the vitni CLI — so this is a compliance check that runs on every commit, not a
benchmark. Step 3 is the one that matters most: re-ingesting the raw material a
deleted item came from is how systems quietly bring it back, and the
value-keyed tombstone drops it at the write boundary regardless of what the
extractor re-produces.

One bound worth stating exactly: the chunk cache is pre-redaction *by
necessity* (quote verification needs the raw text), so before this step
joined the protocol a forgotten value's bytes could sit in the cache until
the age reaper fired. Now `forget` purges the local chunk cache in the same
command, and the `chunk_cache_days` reaper (default 3 days) remains the
independent upper bound for anything written after a forget. The claim is
scoped to this machine: the cache never syncs anywhere.

## Decisions carry their because

A decision without its reasoning invites the next session to re-litigate it.
When the transcript *states* the why, capture keeps one short clause of it:

```text
- [✓ verbatim] soft-clip over hard clamp — because the clamp erased ordering in tied groups
```

The honesty bar matches everything else: stated reasoning only, never
invented — a decision whose why was never said arrives without one.

## The redaction boundary, stated exactly

"A quoted secret never reaches disk" is easily heard as "secrets never leave
the machine". Those are different claims, and only the first is made:

- Redaction is a **disk boundary**. It runs where bytes are persisted or
  displayed from disk — checkpoint writes, the team dual-write, event notes,
  and the status lines read back from crash/error logs.
- It is **not a network boundary**. The serialize call ships the **raw
  session transcript** to whatever LLM backend you configured — a local
  backend sees everything, and so does a hosted one. Pick the backend with
  that in mind.
- It catches **secret shapes, not sensitive meaning**. The pattern list is
  deliberately narrow (see [team sharing](../team/team.md) for the exact
  inventory): filesystem paths, usernames, hostnames, and emails are not
  secret shapes, and a stored quote is arbitrary transcript bytes that syncs
  verbatim to a team remote. `forget` is the tool for content the patterns
  cannot know about.
- The one bounded exception on disk is the pre-redaction **chunk cache**
  described above: local-only, mode 0600, age-reaped, purged wholesale by
  `forget`.


How to run the verbs is in the [guide to correcting memory](../guides/correct-memory.md).
