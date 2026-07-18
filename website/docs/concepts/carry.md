---
sidebar_position: 2
---

# Carry and staleness

A checkpoint captures one session. Carry is what makes memory span many: items
that are still open — unresolved questions, standing decisions, unfinished
work — are carried forward from older checkpoints into the next briefing, so
they keep appearing until something closes them.

## The `[carried]` suffix

An item written by the most recent session appears plain. An item riding
forward from an older checkpoint gets a visible suffix:

```
- [~ inferred] The staging config drift needs an owner [carried]
```

`[carried]` means: *no session has re-confirmed this recently — it may be
stale.* The older a carried item gets, the more skeptically it should be read.
Carry deliberately never rewords items — a carried verbatim quote is the same
bytes it was on day one (see [trust classes](./trust-classes.md)).

## Carry is bounded, not infinite

Unclosed items do not accumulate forever:

- Each item's carry weight **decays** over time, importance-graded. With the
  default floor (`DAIMON_CARRY_FLOOR`), decisions expire from carry in roughly
  5–6 weeks; escalated open questions live around 3–4 months.
- At most `DAIMON_CARRY_MAX` items per kind are carried (default: 8), so a
  briefing stays skimmable no matter how long a project runs.
- [Resolving](./lifecycle.md) an item ends its carry immediately — that is the
  intended way items leave, decay is the backstop.

All knobs live in the
[configuration reference](../getting-started/configuration.md), including
`DAIMON_CARRY` (master switch, on by default).

## The staleness warning

Carry has a failure mode daimon warns about explicitly: an item can ride
along, restated briefing after briefing, without anyone actually re-checking
it against the world. Two of daimon's own artifacts agreeing — the briefing
and an old checkpoint — is **not corroboration**; they are the same source
repeated.

So when a carried item's last-verified stamp ages past `DAIMON_STALE_DAYS`
(default: 7 days), the briefing says so:

```
N carried item(s) unverified for >N days — world-check before repeating as true
```

The intended response is to check the world — code, git, the issue tracker —
and then either [resolve](./lifecycle.md) the item (it's done or wrong) or
`daimon reverify` it with evidence (it's still true), which resets the clock.

## Supersession: carry that argues with itself

When a newer session contradicts a carried item — the project committed to X,
then later committed to Y — carry does not silently drop the old item or
silently keep injecting it as fact. The briefing flags it as a **supersession
candidate**, presenting both sides with the confirm/reject commands inline.
Confirming (`daimon resolve <id>`) withholds the stale side from every future
briefing; rejecting (`daimon reverify <id>`) keeps the item and records why.
Nothing is guessed on your behalf — see the
[item lifecycle](./lifecycle.md) for the full mechanics.
