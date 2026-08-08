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

## `daimon handoff` — the baton

Checkpoints are reconstructive: extracted from the transcript, ranked,
budget-trimmed, competing for slots. The moment of deliberate handoff —
"next session: do THIS first, watch out for THAT" — has different semantics:
intentional, small, imperative, and it must never lose rank to ambient noise.

```sh
daimon handoff "Ship the release first. Beware: the cache key rotated."
daimon handoff --clear
```

The baton leads the next briefing, above every section:

```text
HANDOFF (left deliberately by previous session, 2026-08-03T03:43:58Z):
→ Ship the release first. Beware: the cache key rotated.
```

It is stored as an event, never as a cognitive item — so it cannot enter
ranking, dedup, or carry scoring, and it cannot resolve anything. One baton
per project; a new one supersedes the old (the event trail keeps history).
It stays active until the session that read it ends and serializes — a
crashed session never consumes it. Capped small on purpose: a baton is
"do X, beware Y", not a second checkpoint.

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
