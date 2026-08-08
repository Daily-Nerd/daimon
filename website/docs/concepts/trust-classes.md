---
sidebar_position: 1
---

# Trust classes

Every item in a briefing carries a trust class — a visible marker of *how the
item came to exist*. This is the core idea in daimon: memory that tells you
which parts of it are quotes and which parts are guesses.

## The three classes

### `[✓ verbatim]`

An exact quote from a past session's transcript, pinned at capture.
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

What the check actually guarantees, precisely: the quote is **found in the
transcript after both sides are folded the same way** — case, whitespace runs,
markdown emphasis and list markers, and curly-quote/dash glyph variants are
normalized; a quote elided with `…` is split into fragments that must appear
in order, with very short fragments dropped as too generic to pin. It is a
mechanical presence check, not a byte-for-byte guarantee and not a truth
claim about the world. Byte-immutability applies to *storage*: once pinned,
the stored quote is never reworded by carry, rendering, or truncation.

The guarantee extends past write time: with [receipts](./receipts.md) enabled,
the checkpoint's exact bytes are signed when written — so if a checkpoint file
is edited after the fact, briefing-time verification notices, and the affected
`✓ verbatim` labels are **visibly degraded** rather than silently trusted.

## The corroboration badge — a second axis

Some briefing lines carry an extra annotation beside the trust tag:

```
- [~ inferred] The staging config drift needs an owner [carried] [≈ corroborated ×2]
```

That badge counts **independent sightings** of the claim: how many separate
sessions have observed it, including the session that first wrote it. It is a
different question from the trust class, which answers *what kind of evidence
backs this*. The two never merge — the line above is a corroborated item that
is still `~ inferred`, and it will stay inferred no matter how many sessions
agree.

**The badge is not a promotion.** A trust class changes by exactly two routes:
an evidence-gated `daimon reverify`, or explicit human action. Agreement is
not evidence about the *kind* of a claim, so no amount of it moves the tag.

What earns the badge:

- A later session **independently restates** the claim in its own words or the
  same ones, and that restatement is itself a verified verbatim quote from
  *that* session's transcript.
- The claim's **first writer is provably someone else** — every item records
  the session that originally wrote it, and a session cannot corroborate
  itself.
- The total reaches **two** — the origin of record plus at least one
  independent witness.

What never earns it:

- **Daimon's own echoes.** A recall injection or a briefing block printed into
  the transcript is daimon's output, not a witness. Quote verification strips
  daimon's injected spans before it checks anything, so a restatement copied
  out of a briefing is downgraded to `~ inferred` and cannot corroborate.
  Excluded by construction, not by a check that could be skipped.
- **[Carry](./carry.md) survival.** An item riding forward from session to
  session is one claim copied N times, not N sightings.
- **Teammates.** A synced teammate's checkpoint is unverifiable on your
  machine — their verbatim claims arrive clamped to `inferred`, and their
  origin session does not exist in your checkpoint directory. Team
  corroboration is not in this version.

Demotion outranks the badge. Anything that contradicts an item — a
[resolution](./lifecycle.md), a `superseded-by` verdict, a flagged likely
supersession, a state-changed-since-capture note, a tombstone — zeroes the
count from that moment on, and the contradiction renders alone. "Three
sessions agreed" printed next to "this is probably wrong" reads as support for
the claim, which inverts the signal. Reopening an item does **not** restore
what it lost; corroboration has to be re-earned by a witness, not by a status
change.

The count is never stored on the item. It is derived at read time from the
append-only event log, where each corroboration is one row naming the session
that agreed and the item it agreed about — so the witnesses are auditable, and
no edit to a checkpoint file can invent a number the log does not support.

Corroboration also stays out of ranking. It does not raise an item's score, in
the briefing or in recall. A badge that lifted an item would surface it more
often, which would inject it into more transcripts, which would produce more
restatements — a loop that measures how often daimon showed an item to itself
rather than anything about the world.

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

## Ground, not only quicksand

The inverse marker exists too. At brief time daimon spot-checks carried
claim-bearing items against reality — PR states, branch and file existence,
receipt validity. A contradiction replaces the item's render with what
actually changed. A *confirmation* used to render nothing at all, which made
a just-verified claim indistinguishable from an unchecked one; now it earns a
quiet suffix:

```text
- [~ inferred] PR #60 awaiting review [carried] [✓ world-checked]
```

`[✓ world-checked]` means the world itself agreed with this claim during this
brief — a separate axis from the trust class (how the claim was captured) and
from corroboration (how many sessions witnessed it). You can lean on these
without re-verifying. One asymmetry is deliberate: a contradiction on any
axis suppresses the badge — quicksand always outranks ground.
