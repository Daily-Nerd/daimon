---
sidebar_position: 4
description: "Close a loop, re-assert a claim, settle a supersession, remove content, lift a forget or leave a baton: which command fits and how to run it."
---

# Correct memory

Daimon never guesses a transition for you, so each change to what a briefing carries has its own command. The contract behind them is in [the item lifecycle](../concepts/lifecycle.md).

| you want to | run |
| --- | --- |
| close an item that is done | `daimon resolve` |
| say a carried item is still true | `daimon reverify` |
| record that an open item moved on | `daimon amend propose`, see [decide and ratify](./decide-and-ratify.md) |
| remove content for good | `daimon forget` |
| lift a forget on this machine | `daimon unforget` |
| tell the next session what to do first | `daimon handoff` |

Both `resolve` and `forget` accept an exact id or a free-text query, and a query must match exactly one item. Add `--dry-run` first to see what would match.

## `daimon resolve`: close a loop

```sh
daimon resolve "retry policy for the payments webhook" --dry-run
daimon resolve o-3f8a2c --note "shipped exponential backoff in #212"
```

Resolving records an append-only event; from then on, briefings **withhold**
the item instead of carrying it stale. The item is not deleted, its history
stays searchable, and the event trail shows when and why it closed.
`--status` accepts a free-form lifecycle status; any status starting with
`reopen` revives the item. A status starting with `forgotten:` is refused:
that is a tombstone, and only `daimon forget` writes one (it also removes
the value from the files). Only `daimon unforget` lifts one.

## `daimon reverify`: assert it's still true

```sh
daimon reverify o-3f8a2c --evidence "checked the release page"
```

Reverify is the answer to the [staleness warning](../concepts/carry.md#the-staleness-warning):
a carried item aged past the threshold, you checked the world, and it still
holds. The event resets the item's last-verified stamp, so the warning clock
restarts. Reverify takes **exact ids only**, re-asserting a claim is
deliberate, so there is no fuzzy match to mis-fire.

Reverify is also the **reject** half of a supersession candidate (below).

## Supersession candidates

When a newer session contradicts a carried item, the briefing presents a
**supersession candidate**: both sides, with the confirm/reject commands
inline. You verify which side is true in the world, then answer with exactly
those commands:

- **Confirm**, `daimon resolve <id>`: the old item is genuinely superseded;
  future briefings withhold it.
- **Reject**, `daimon reverify <id>`: the contradiction was apparent, not
  real; the item stands, freshly verified.

The design principle across the whole lifecycle: daimon flags, you decide.
Contradiction, staleness, and removal are all surfaced with evidence and
resolved by an explicit human (or explicitly-instructed agent) action , 
never by a silent merge.

## `daimon forget`: remove content

Resolve closes an item but keeps its content in history. Forget removes the content itself, and leaves a hash-only tombstone. What it removes, and the guarantee that it stays removed, are in [the item lifecycle](../concepts/lifecycle.md#daimon-forget--remove-provably).

```sh
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

### `daimon unforget`: lift a forget

```sh
daimon unforget o-3f8a2c --reason "forgot the wrong one"
daimon unforget o-3f8a2c --dry-run
```

Unforget takes the id the forget receipt printed, from a person at a terminal
(an agent has no way to run it), and lifts the forget on every ref that carries
its key. It restores nothing: the value was removed from every surface, so the
lift only lets a new capture of the same claim back in. It exits 0 only when
nothing else still withholds the value; if another project on this machine, a
teammate's published row or a ledger it could not prove does, it says which
and exits 4. A published tombstone row has no retraction, so the row you
published stays and your teammates keep withholding the value until they lift it.

## `daimon handoff`: the baton

Checkpoints are reconstructive: extracted from the transcript, ranked,
budget-trimmed, competing for slots. The moment of deliberate handoff , 
"next session: do THIS first, watch out for THAT", has different semantics:
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

The header says who wrote it. A baton written from a terminal reads as above.
A baton written without a terminal, or with `daimon handoff --by agent`, reads
`HANDOFF (written by an agent, unverified, <time>):` and should be taken as an
agent's note, not a person's instruction. Batons stored before this was
recorded carry the legacy `cli` source and keep the first wording.

It is stored as an event, never as a cognitive item, so it cannot enter
ranking, dedup, or carry scoring, and it cannot resolve anything. One baton
per project; a new one supersedes the old (the event trail keeps history).
It stays active until the session that read it ends and serializes, a
crashed session never consumes it. Capped small on purpose: a baton is
"do X, beware Y", not a second checkpoint.
