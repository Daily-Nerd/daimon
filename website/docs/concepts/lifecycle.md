---
sidebar_position: 4
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

## `daimon resolve` — close a loop

```sh
daimon resolve "retry policy for the payments webhook" --dry-run
daimon resolve o-3f8a2c --note "shipped exponential backoff in #212"
```

Resolving records an append-only event; from then on, briefings **withhold**
the item instead of carrying it stale. The item is not deleted — its history
stays searchable, and the event trail shows when and why it closed.
`--status` accepts a free-form lifecycle status; any status starting with
`reopen` revives the item.

## `daimon reverify` — assert it's still true

```sh
daimon reverify o-3f8a2c --evidence "checked the release page"
```

Reverify is the answer to the [staleness warning](./carry.md#the-staleness-warning):
a carried item aged past the threshold, you checked the world, and it still
holds. The event resets the item's last-verified stamp, so the warning clock
restarts. Reverify takes **exact ids only** — re-asserting a claim is
deliberate, so there is no fuzzy match to mis-fire.

Reverify is also the **reject** half of a supersession candidate (below).

## `daimon forget` — remove, provably

```sh
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

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
  when, and why (`--reason`, redacted like any note).
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
  never fatal — removal of the belief state always completes — and the
  command reports honestly whether the purge succeeded.
- Briefing withhold, carry suppression, and `daimon stats` all inherit the
  tombstone through the same event stream.

### Deletion-durability compliance

The claim "a forgotten memory stays forgotten" is not asserted — it is
committed as an executable protocol. `plugin/tests/test_deletion_durability_protocol.py`
runs a forgotten value through every path that could quietly resurrect it and
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

## Supersession candidates

When a newer session contradicts a carried item, the briefing presents a
**supersession candidate**: both sides, with the confirm/reject commands
inline. You verify which side is true in the world, then answer with exactly
those commands:

- **Confirm** — `daimon resolve <id>`: the old item is genuinely superseded;
  future briefings withhold it.
- **Reject** — `daimon reverify <id>`: the contradiction was apparent, not
  real; the item stands, freshly verified.

The design principle across the whole lifecycle: daimon flags, you decide.
Contradiction, staleness, and removal are all surfaced with evidence and
resolved by an explicit human (or explicitly-instructed agent) action —
never by a silent merge.
