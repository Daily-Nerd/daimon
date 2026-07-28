---
slug: verbatim-vs-inferred
title: "Verbatim vs. inferred: the trust class your agent's memory needs"
authors: [daimon]
tags: [concepts, trust-classes]
---

Your agent opens a session and tells you what you decided last time. Some of
that is a quote. Some of it is the model's summary of a quote. Some of it is a
conclusion the model drew at 2am from a session it was already losing track of.

All three arrive in the same font, in the same confident tone, with no way to
tell them apart. That is the actual problem with agent memory, and it is not
solved by remembering more.

{/* truncate */}

## Two populations, two failure modes

Daimon tags every item it carries with a trust class, visible on the line
itself:

```
- [✓ verbatim] PR #60 awaiting review  — "review requested 2026-07-01"
- [~ inferred] The retry storm was caused by a shared deadline across three calls
```

The tag is not decoration. The two classes fail in genuinely different ways:

- A **verbatim** item can be *stale* — the world moved on since the quote was
  said — but it cannot be *misremembered*. The quote is what was said.
- An **inferred** item can be stale *and* wrong. The model may have misread the
  session at the moment it wrote the summary down.

That difference should change how you act on a line. A stale quote needs a
world check. A wrong inference needs to be thrown away. Collapsing both into
"the memory says" destroys the distinction exactly when you need it.

## The checker has to be dumber than the thing it checks

A trust class is worthless if the model assigns it to itself. A model that
hallucinates a quote will also happily label the hallucination `verbatim`.

So the model never gets a vote. At serialize time every candidate quote is
checked against the rendered transcript by a deterministic verifier: pure
string operations, no LLM, no judgment. A quote that matches gets the stamp. A
quote that does not match is **downgraded to `~ inferred`** on the spot and
kept, not deleted. The claim survives; the certification does not.

This is the principle the whole design rests on. The checker must be dumber
than the thing it checks, because anything smart enough to be fooled by the
extractor is not a check.

## The harder problem: a perfect quote can be perfectly false

Here is the part we got wrong for months, and the reason this post exists.

Verbatim matching certifies **transcription, not truth**.

The failure that taught us: an agent finished a long session and wrote
"serialization succeeded" into its own memory. It had not succeeded. The next
session read that line, believed the memory layer was healthy, and built on a
foundation that was not there.

Every step of that is faithful. The model did say it. The transcript records it
exactly. The quote verifier matched it character for character and stamped it
`✓ verbatim`, correctly. The trust class did its job and the memory was still
false, because the thing being certified was that the sentence was *said*, not
that the event *happened*.

## Outcomes need a witness, not a quote

As of 0.20, claims that assert a completed outcome get held to a second
standard.

If an item's text asserts something finished — succeeded, merged, deployed,
tests green, shipped — it has to cite a concrete signal from that same session:
a tool result, an exit status. Something the session actually produced rather
than something the model concluded.

An outcome claim that cites a real signal stays `verbatim`. An outcome claim
with no citation, in a session that *did* surface signals, gets downgraded to
`~ inferred`. The quote and its verification stamp stay attached, because the
transcription is still honestly attested. It is the outcome that is unwitnessed.

An unwitnessed outcome is a report, not a fact.

Two deliberate limits on that rule, both in the direction of doing nothing
rather than guessing:

- **Hedges are not assertions.** "will be merged" is a plan. "whether the
  deploy succeeded" is a question. Neither is touched.
- **Signal-free sessions never downgrade.** Some hosts surface no parseable
  tool results at all. Grounding is impossible there, and absence of evidence
  about the *host* is not evidence against the *claim*.

## What none of this fixes

Trust classes tell you where a claim came from. They tell you nothing about
whether it is still true.

A `✓ verbatim` quote with a real tool-result behind it is fully attested and
goes stale the moment someone merges the PR it describes. Provenance is not
currency, and pretending otherwise would be the same mistake one layer up.

That is why every briefing opens with a **VERIFY BEFORE TRUSTING** block rather
than a summary, and why 0.20 adds an opt-in spot-check that re-reads external
state at briefing time and visibly flags carried claims the world has since
contradicted. We are measuring how often that fires before we say anything
about how big the problem is.

## Try it

```bash
uv tool install 'daimon-briefing[pretty]'
```

The full mechanics live on the [trust
classes](/docs/concepts/trust-classes) page, with
[carry and staleness](/docs/concepts/carry) for the currency half and
[receipts](/docs/concepts/receipts) for what happens if a checkpoint is edited
after it is written. Code and issue tracker:
[Daily-Nerd/daimon](https://github.com/Daily-Nerd/daimon).
