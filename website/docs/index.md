---
sidebar_position: 0
sidebar_label: Overview
slug: /
---

# daimon

Your agent forgets everything between sessions. daimon writes a small
**checkpoint** when a session ends and turns it into a **briefing** when the
next one starts — so the agent resumes from a faithful prior state instead of
a confident guess:

```
While you were away — here's where we left off.

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"
- [~ inferred] The staging config drift needs an owner [carried]

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

Active topic: Migrating the scheduler off cron to the new worker pool
```

Three nouns cover the whole system. A **session** is one conversation with
your agent. A **checkpoint** is the local, signed record a session leaves
behind when it ends. A **briefing** is the skimmable rendering of the latest
checkpoint, injected when the next session starts.

## Why it's different

Most memory tools store what a model *wrote about* what happened — and when
that text is wrong, nothing tells you. daimon marks every line with how it
came to exist: `[✓ verbatim]` lines are exact quotes checked
character-for-character against the session transcript by a deterministic
verifier (pure string operations, no LLM); `[~ inferred]` lines are the
model's own conclusions, honest about being derivations. A "quote" that fails
the check is demoted to inferred on the spot, so a hallucinated quote can
never wear the verbatim badge. With [receipts](concepts/receipts.md) enabled,
the checkpoint's exact bytes are signed — everything above is checkable
offline, without trusting daimon, the model, or this page.

## The loop

1. **A session ends** — a host hook serializes the transcript into a
   checkpoint. Local JSON on your disk; no server, no telemetry.
2. **The next session starts** — a hook injects the briefing as context, so
   the agent answers "where did we leave off?" before you ask.
3. **You verify, resolve, or correct** — open items carry forward until
   closed, and go visibly stale instead of lying forever.

**Start with the [Quickstart](getting-started/quickstart.md)** — install to
first briefing in five steps. Per-host setup lives in
[Hosts](hosts/index.md); the trust system is explained in
[Trust classes](concepts/trust-classes.md).
