---
slug: negative-knowledge
title: "Your agent's failures are its most valuable memory, and almost nobody stores them"
authors: [daimon]
tags: [concepts, scars, negative-knowledge]
---

Everyone building agent memory is building the same thing: remember what
worked, remember what was decided, remember who prefers tabs. Useful. But the
expensive amnesia is not forgetting successes. It is forgetting failures.

An agent that forgets a success re-derives it in a few hundred tokens. An
agent that forgets a failure re-attempts it: the refactor that broke prod, the
"obvious cleanup" that was load-bearing, the dependency upgrade that was
abandoned twice for the same reason. You pay for the same dead end every time
a fresh session walks into it confidently.

{/* truncate */}

## The field just noticed

Three papers landed on this in the last two months.

An ICML 2026 AI4Research workshop paper ([arXiv:2606.21024](https://arxiv.org/abs/2606.21024))
names the object directly: *negative knowledge*. Its diagnosis of automated
research systems applies word for word to coding agents: failures "appear as
local debugging signals but rarely become durable research objects." Their
fix is a memory layer "explicitly designed for failure," with typed records
and a curator separated from the agent that failed, "to reduce
self-assessment bias." The concept paper the space needed. It also, honestly,
leaves the operational questions open: no staleness policy, no expiry model,
and its own tables show a failure record from one system misleading another
(negative transfer). The concept is established. The lifecycle is not.

A field report from a production team ([arXiv:2607.13091](https://arxiv.org/html/2607.13091v1))
turned accepted review comments into persistent behavioral rules, with a
qualification heuristic worth stealing verbatim: *"Would this mistake
plausibly recur in a different context? If yes, it becomes a rule."* Their
candor is also worth stealing: no control group, small sample, and this
warning, which every memory system should frame and hang on the wall: "a
noisy review culture can poison the rule set faster than the validation step
can catch."

And [PROJECTMEM](https://arxiv.org/abs/2606.12329) shipped the closest thing
to our design: an append-only, git-native, plain-text event log with a
deterministic pre-action gate, "memory that does not merely answer the agent
but acts on its next action." They call the category *Memory-as-Governance*.
It is the right name. (Related, on the write side:
[GovMem](https://arxiv.org/abs/2607.02579) governs memory promotion with a
promote / reject / needs-review decision, which is structurally the same gate
we will describe below on the human side.)

So the ground is not empty, and we are not claiming a first. What we can
offer is a design that has been running in a real repo for a month, with the
scar tissue to show for it.

## How we do it: author, promote, fire

Our version is two tools with a human in the middle.

**Agents author during work.** When a session abandons an approach, keeps
deliberate weirdness on purpose, or steps on non-obvious coupling, the agent
records it as a candidate scar right then, under an authoring contract: a
dead end needs evidence of the attempt and the abandonment, a landmine needs
both coupled sites named, and first-person transcript prose gets dropped
because a feeling is not a claim about code. Every active scar in daimon's
repo was written this way, during the session that earned it.

**About automatic harvesting, honestly.** We also built a zero-LLM harvester
that mines session checkpoints for candidates, because cold-start repos have
no sessions to author from. Its field record so far is mostly noise: the
first tally was 4 candidates, 0 promotable, and the false-trigger log is
longer than the keep list. A qualification filter shipped in response,
enforcing the same structural obligations at machine-write time, and its
results are still accumulating. We tell you this because the receipts are
the point: we measured our own tooling, it missed the bar, we gated it, and
now we measure the gate too. A memory system that cannot reject its own
writes is the
poisoning vector the behavioral-rules paper warns about.

**A human promotes.** Candidates land in `.scars/candidates/`, never in the
active set. Promotion is a deliberate human act. This is the same conclusion
the behavioral-rules paper reached from the other direction: their rule
quality "depends on human review quality," and bad rules amplify errors. An
auto-promoted failure memory is a poisoning vector with a workflow diagram.

**Scar fires.** [Scar](https://github.com/Daily-Nerd/Scar) is the
enforcement half: promoted scars carry anchors, and when an agent is about to
edit anchored code, the scar is injected before the edit tool runs. Not at
commit time, when the mistake is already made and staged. Before the edit.

That last distinction is not ours alone; it is PROJECTMEM's own roadmap.
Their future-work section describes "moving it to the agent's tool-call
boundary (a pre-action hook)" so the gate can warn "the instant a change
begins to resemble a previously-failed one—intervening before the edit, not
at commit time." That is the gate Scar ships today. To be equally plain about
the other column: PROJECTMEM has a published paper and a couple hundred
stars; Scar has three. This is a design note, not a maturity claim.

## The two pieces we have not seen anywhere else

**A falsification condition on every scar.** Each scar records
`expires.condition`: the specific change that would make it obsolete, plus a
review date the linter enforces. The condition itself is authored discipline
today, not yet machine-checked; the point is that it exists at authoring
time. Negative knowledge rots differently than positive knowledge; a fact
that goes stale is wrong, but a warning that goes stale is friction that
trains everyone to ignore warnings. Neither paper above has an expiry model.
One grows its rule set monotonically and never removes anything. The
workshop paper does not treat staleness at all. Writing down the condition
under which a warning should die is, as far as we can tell, still unclaimed
ground.

**The fence.** Dead ends and landmines are failure memory. The third scar
type is not: a *fence* protects code that looks wrong on purpose. The
sub-second timeout that looks too tight but bounds a real budget. The
duplicated block that two systems must not share. Failure memory tells an
agent "do not repeat this attempt." A fence tells it "do not clean this up."
No failure-record schema we have found represents that, and it fires
constantly in practice, because cleaning up intentional weirdness is exactly
what a capable agent wants to do.

## What we are not claiming

There is no measured prevention rate. Not ours, and not anyone's: PROJECTMEM
names "failures-prevented-per-commits" as the missing benchmark of the whole
category, and the behavioral-rules paper titles a section "The Missing
Benchmark." We agree, and we are not going to fill the gap with a vibe. What
we have are receipts of individual catches: a scar that flagged a regex
denial-of-service risk that had passed both unit tests and review, and a
fence that fired mid-build and changed a wizard's timeout floor the same
afternoon. Anecdotes, labeled as anecdotes.

If you want the failure half of your agent's memory to exist at all:
[Scar](https://github.com/Daily-Nerd/Scar) ships the authoring contract as a
loadable skill and fires the promoted scars,
[daimon](https://github.com/Daily-Nerd/daimon) drafts cold-start candidates
from session checkpoints, and the format is
[a page of YAML and prose](https://github.com/Daily-Nerd/Scar/blob/main/SCAR-FORMAT.md)
you could implement yourself in an afternoon. The dead ends you have already
paid for are the cheapest knowledge you will ever ship.
