---
sidebar_position: 6
description: "Refutations record what is NOT true or what NOT to do, cited by evidence, never decayed. How they render, how to inspect one, and the boundary with a repo's own .scars/ directory."
---

# Negative knowledge

Most of what daimon remembers is positive: this is true, this happened,
this is still open. A refutation is the opposite kind of fact. It records
that an approach was tried and rejected, that a claim turned out false, or
that a path should not be taken again, and it names the evidence that
settled the question.

```sh
daimon refute add --subject "original #502 receipt design" \
  --scope "carried-item receipt tiers" --anchor issue:502 \
  --evidence "measurement:566/623 origin misses" --ratify
```

(the flag that carries the rejected claim's own text sits between `--subject`
and `--scope`; see the [CLI reference](../reference/cli.md) for the exact
syntax.)

Refutations live on the same ledger as [standing rulings](./rulings.md): one
append-only stream, one id space, one deletion path. A ruling is a positive
constraint a human put in force. A refutation is a negative one: what no
longer holds, and why.

## Evidence is cited, not verified

Every `--evidence` source has to be a typed reference: `message:<id>`,
`transcript:<session>`, `artifact:<path>`, `issue:<number>`,
`measurement:<receipt>`, `receipt:<id>`, or `url:<source>`. daimon checks the
*shape* of that reference and nothing else. It never resolves the issue,
never re-runs the measurement, never confirms the artifact still exists at
that path. The citation is recorded exactly as given, permanently, so anyone
reading the refutation later knows precisely what was checked when the
question was settled, and can go check it themselves.

That is also why an agent's own assertion never activates anything by
itself. Citing evidence is not the same claim as *this evidence actually
supports the conclusion*, and daimon does not decide that second question for
you.

## Candidate, then active

```sh
daimon refute add --subject "…" --scope "…" \
  --evidence issue:502 --by agent
daimon refute ratify r-1a2b3c4d5e6f
daimon refute revise r-1a2b3c4d5e6f --evidence issue:530
daimon refute overturn r-1a2b3c4d5e6f --evidence "measurement:new-result"
```

An agent's `add` records a **candidate**: written, searchable, but not yet
in force, and never rendered as an active guard until a human ratifies it.
The human path is `ratify` from an interactive terminal; there is no way for
an agent to self-promote a candidate. `revise` appends a new,
evidence-cited version, which itself needs ratifying before it takes over.
`overturn` cites evidence against an active refutation: an agent's overturn
is recorded as a proposal and the guard stays in force; a human's overturn
deactivates it immediately.

## Reading the ledger

```sh
daimon refute list
daimon refute show r-1a2b3c4d5e6f
daimon refute search receipt verification
daimon refute guard "should we revisit #502?"
```

`list` and `show` read this project's own refutations. `show` on one record
gives its full evidence citations, its scope and anchors, and who wrote and
who activated it, in one place, since nothing on this ledger decays or gets
re-extracted: the record you read is the whole history that matters. `search`
is the topic-addressable pull, and it deliberately returns both polarities
labelled, refutations and rulings together, since mid-session, after a
briefing has scrolled past, either kind of record might be the one worth
surfacing. `guard` checks a proposed action's anchors or subject phrase
against active refutations by exact match only, advisory and never blocking
a command on its own; it exists for an agent to check its own next move
before taking it.

The `daimon why`, `daimon diff`, and `daimon blame` commands (see the
[item lifecycle](./lifecycle.md) and the [CLI reference](../reference/cli.md))
answer a different question: they inspect checkpoint items across the
generations daimon retains. A refutation is not a checkpoint item and does
not roll off into an older generation, so there is nothing for those three
to diff or blame here. `refute show` already is the equivalent: the
complete, un-decayed record for one id.

## The boundary with `.scars/`

A repository can also carry its own negative knowledge, in a `.scars/`
directory checked into the repo itself: a dead end tried and abandoned, a
piece of code that looks wrong on purpose, a landmine that breaks something
non-obvious. Scars ship *with the code they protect*. They travel with the
repository, are visible to anyone who clones it, and answer "what happened
here, in this file, that the next contributor needs to know before they
touch it."

daimon's refutation ledger answers a different question, at a different
layer. It is per-project memory that lives outside the repository entirely,
scoped to whatever a person or agent working in that project has actually
tried and rejected, and it exists whether or not the project keeps scars at
all. The two are complementary, not competing: a scar documents a trap in
the code; a refutation documents a conclusion someone reached while working
on it.

Anchoring a scar to the code it protects, holding candidates, and promoting
one to active status is its own tool's job: [Scar](https://github.com/Daily-Nerd/Scar)
is where that review and promotion tooling lives, not daimon.

daimon ships one bridge between the two layers. With `DAIMON_SCAR_HARVEST=1`,
it drafts negative-knowledge candidates from each session into
`.scars/candidates/` for human review: zero-LLM, path-anchored, and active
only in repos that already have a `.scars/` directory. It writes candidates
for a person to review with Scar; it never promotes one itself.
