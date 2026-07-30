---
slug: origin-bound-corroboration
title: "Agreement is not evidence: shipping origin-bound corroboration"
authors: [daimon]
tags: [concepts, trust-classes, corroboration, release]
---

Here is a trap every agent memory system walks toward sooner or later: an item
that keeps showing up starts to feel true. Five sessions all "remember" the
same fact, so the fact must be solid. Promote it. Trust it more.

We wanted that feature. Independent re-observation *should* strengthen a
memory — that is how evidence works everywhere else. But before building it we
went looking for the ways it breaks, and what we found changed the design, the
release, and one security assumption we had been living with. All of it
shipped today in v0.22.0.

{/* truncate */}

## The echo in our own house

Daimon injects prior memory into new sessions: a briefing at session start, a
`daimon recall:` line when a prompt matches past work. That injected text
becomes part of the new session's transcript. The serializer then reads that
transcript to extract what the session learned.

See the loop? An item from session A gets injected into session B's
transcript, and session B's extraction can "observe" it there — not because
the fact was re-derived from real work, but because daimon quoted itself. On
our own corpus, thirteen transcripts carry injected prior items. A naive
corroboration counter ("different session saw it again") would count daimon's
own echoes as independent witnesses, and the items recalled most often would
accumulate the most confidence. Recall frequency would become truth.

It got worse before it got better. While mapping the injection surfaces we
found that our quote verifier checked verbatim quotes against the *unstripped*
transcript. A quote copied from daimon's own injected line passed verification
and was stored as `verbatim`, `quote_verified: true` — a prior session's item
laundered as freshly witnessed. That hole did not wait for the corroboration
feature; it shipped as a standalone security fix the same day it was found,
and verification now refuses any quote whose only support lies inside
daimon's own output. Failed echoes get their own rejection reason
(`echo-only`), so the echo rate is now measurable instead of invisible.

## The proof that says the naive version cannot be patched

This is not just our bug. A recent paper — [*Securing LLM-Agent Long-Term
Memory Against Poisoning: Non-Malleable, Origin-Bound Authority with
Machine-Checked Guarantees*](https://arxiv.org/abs/2606.24322) — proves, with
machine-checked TLA+ theorems, that defenses based on a memory item's content
or its derivation history are unsound. Attackers launder untrusted origins
through three channels: the agent's own summarization, trusted-tool echoes,
and **manufactured corroboration** — planting agreeing sources so that
agreement reads as verification.

That third channel is exactly the self-reference loop above, named as an
attack primitive with an impossibility result behind it. The paper's repair:
write-time origin binding is *necessary*, and origin-bound authority with
Sybil-resistant corroboration-gated elevation is *sufficient*. In plain
words: fix where a claim came from at the moment it is written, and only
count agreement when independence can be proven from those origins — never
assumed from the agreement itself.

## What v0.22.0 ships

Our implementation of that prescription, in a local-first CLI:

- **Origin bound at write.** Every item is stamped with the session and
  author that first wrote it, at the admission boundary, on the same
  never-rewritten rail as its identity. A model that emits its own origin
  fields gets them stripped — a memory cannot issue itself a witness.
- **Independence proven from origins.** A re-observation counts only when the
  original writer is a *different* session, the new observation is locally
  verbatim with a verified quote (which, after the echo fix, structurally
  excludes daimon's own output), the match is strong enough to certify rather
  than merely deduplicate, and the two observations do not share transcript
  message bindings.
- **An auditable count, never a stored score.** Corroborations are events in
  the append-only log, one per independent witness. The count is derived at
  read time. Contradiction outranks: a superseded or world-checked item loses
  its badge, and reopening it does not restore counts earned before the
  contradiction.
- **A separate axis, not a trust promotion.** Items independently seen twice
  render as `[≈ corroborated ×2]` beside their trust tag. The trust class
  itself never moves on recurrence — that still takes re-verification
  evidence or an explicit human decision.
- **Wired into nothing, on purpose.** The badge affects no ranking and no
  recall scoring, and a test enforces that scoring code cannot even import
  the corroboration reader. The self-reinforcing loop — promotion raises
  salience, salience raises injection, injection manufactures the next
  corroboration — closes exactly where a counter feeds ranking, so that wire
  stays cut until field data says otherwise. We ship the measurement before
  anything acts on the measurement.

## What it does not do

Honesty section, as always. Corroboration only accrues on items written from
v0.22.0 forward — origins are never guessed retroactively, because a wrong
guess would make dependent observations look independent. A resumed session
that replays the same conversation under a new id is refused where hosts
preserve message ids, but a host that mints fresh ids can make one
conversation look like two. An attacker who controls the content of two
separate sessions can still manufacture two origins — the design raises the
cost of fake agreement from one recall line to two compromised sessions; it
does not make fake agreement impossible. And teammate checkpoints do not
corroborate in this version: a synced copy of a claim is still one witness,
and the gates that would make cross-author counting Sybil-resistant are
designed but deliberately not enabled yet.

The full release also carries the write-gateway hardening this work sat on
top of: value-keyed deletion end to end, a write-audit guard over every
command, and inbound team content passing the same scope, redaction, forget,
and trust gates as local writes.

If your agent's memory tells you something twice, ask it who told it first.
[daimon](https://github.com/Daily-Nerd/daimon) can now answer.
