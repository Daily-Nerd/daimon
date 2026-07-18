---
sidebar_position: 1
---

# Trust classes

Every item in a briefing carries a trust class — a visible marker of *how the
item came to exist*. This is the core idea in daimon: memory that tells you
which parts of it are quotes and which parts are guesses.

## The three classes

### `[✓ verbatim]`

An exact quote from a past session's transcript, pinned character-for-character.
Verbatim items are never reworded — not by carry-over between sessions, not by
rendering, not by budget truncation. When a briefing shows

```
- [✓ verbatim] PR #60 awaiting review  — "review requested 2026-07-01"
```

the trailing quote is the actual text from the transcript, and it stays
byte-identical for as long as the item lives.

An agent reading the briefing should repeat verbatim items exactly, never
summarize or paraphrase them.

### `[~ inferred]`

A conclusion the serializing model drew from the session — a summary, a
diagnosis, a connection between events. Inferred items are honest about being
derivations: they are allowed to evolve as later sessions refine them, and they
should be verified against the world (code, docs, the issue tracker) before
anything load-bearing is built on them.

### `[? untagged]`

An item that never had trust recorded — typically from an older checkpoint
written before trust classes existed, or from a degraded capture. Treat
untagged items like inferred ones: verify before relying on them.

## Why the distinction matters

Most memory systems store one kind of thing: text a model wrote about what
happened. When that text is wrong — and models summarizing long sessions are
wrong regularly — there is no way to tell from the memory itself. It all reads
with the same confidence.

Trust classes split the memory into two populations with different failure
modes:

- A **verbatim** item can be *stale* (the world moved on since the quote was
  said) but it cannot be *misremembered* — the quote is what was said, provably.
- An **inferred** item can be both stale *and* wrong — the model may have
  misread the session when it wrote it.

That difference changes how a reader (human or agent) should act on each item,
which is why the briefing makes it visible on every line instead of burying it
in metadata.

## Verification is mechanical, not claimed

Verbatim status is not the extracting model's opinion of itself. At serialize
time, every verbatim item's quote is checked against the rendered transcript
by a deterministic verifier — pure string operations, no LLM, on the principle
that *the checker must be dumber than the thing it checks*. A quote that
verifies gets stamped; a quote that doesn't is **downgraded to `~ inferred`**
on the spot, so a hallucinated "quote" can never wear the verbatim badge.

The guarantee extends past write time: with [receipts](./receipts.md) enabled,
the checkpoint's exact bytes are signed when written — so if a checkpoint file
is edited after the fact, briefing-time verification notices, and the affected
`✓ verbatim` labels are **visibly degraded** rather than silently trusted.

## VERIFY BEFORE TRUSTING

Briefings open with a section of items describing state that may have changed
*outside* the session — merged PRs, rotated keys, moved files. A verbatim tag
means the quote is faithful; it does not mean the world still looks like that.
The intended reading protocol, for humans and agents alike:

1. Read the item.
2. Check the world (files, git, the issue tracker) before repeating it as
   current fact.
3. [Resolve](./lifecycle.md) it once it is closed, so it stops carrying.

A briefing is context, not instructions — it never overrides what the user is
asking for now.
