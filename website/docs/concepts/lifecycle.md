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
- Briefing withhold, carry suppression, and `daimon stats` all inherit the
  tombstone through the same event stream.

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
