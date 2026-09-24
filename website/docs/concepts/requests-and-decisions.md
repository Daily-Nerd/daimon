---
sidebar_position: 7
description: "Cross-project requests, the decide queue, loops, amendments, and freeform log events: how one project asks another for something, and what stays waiting on a human."
---

# Requests and decisions

A project's memory does not stop at its own boundary. One project can ask
another for something, and daimon tracks both sides of that ask, plus a
separate queue for everything else that is waiting on a human decision
inside a single project.

## Cross-project requests

A request lives in the **sender's** own project bucket: "this project asks
that one for a thing, and here is why." The recipient never writes into the
sender's ledger. It discovers the ask by reading the sender's bucket at
brief time, and answers with its own decision, recorded in its own bucket.
Every request therefore spans two buckets, and the combined view either side
sees is assembled at read time; nobody ever writes into another project's
ledger.

```sh
daimon request open --to <dir> --ask "…" --why "…"
```

`--to` takes the recipient's project directory, validated against the
projects daimon already knows about, with a suggestion on a likely typo.
Two kinds of ask exist:

- **`work`**, the default: asks the recipient to change something or spend
  effort, and it waits on a person there before it counts as accepted.
- **`info`**: answerable from the recipient's own existing artifacts, and it
  owes no accept at all. Opening an `info` ask normally requires a human
  channel on the sender's side too. An agent may open one only under an
  active `verb=open` [ruling](./rulings.md) its own project ratified for
  that recipient, and the record then says so plainly, so a ruling-opened
  ask never looks like one a person opened by hand.

Once opened, a handful of verbs move the record forward:

- **`revise`** answers a needs-info reply, or sharpens the ask, up to three
  times per record; a fourth attempt opens a new request with
  `--supersedes` instead, so the lineage stays visible rather than silently
  rewritten.
- **`accept` / `reject` / `needs-info`** are the recipient's decision, and
  they are human-only: an interactive terminal is required. The one carved
  exception is an addressed `info` ask still open, or in needs-info, which
  an agent may accept directly; a `work` ask can only be accepted by an
  agent when an active [request-policy ruling](./rulings.md#request-policies-are-a-ruling-class-too)
  names that exact sender, and the record then names the ruling too.
  Rejection is final for that record: the sender opens a new request rather
  than asking again.
- **`suppress`** drops a request out of the recipient's own briefing panel,
  attention only, human-only; the record stays fully visible in `list` and
  `inbox`, and any later decision reverses the suppression.
- **`done`** reports the ask as satisfied, from either side. An agent's
  claim renders as unverified until the recipient's next session-end
  byte-checks the evidence quote against its own transcript; a human `done`
  renders as stated. On a `work` request nobody has accepted yet, an
  agent's `done` records the claim but leaves the decision queue open,
  waiting for `accept` or `reject`.

```sh
daimon request list    # this project's own sent requests
daimon request inbox   # requests addressed TO this project
```

Both take `--json`. The briefing itself carries two small panels drawn from
the same data: the recipient's "requests waiting on you," and the sender's
"decisions on requests you sent," each capped with a loud overflow line
rather than a silent drop. The [MCP server](../reference/mcp.md) exposes the
recipient's view read-only, as the `requests_inbox` tool; no request-writing
verb is reachable over MCP, and `daimon_brief` itself never carries request
content.

## `decide`: what is waiting on you

`daimon loops` lists this project's own open, addressable loop items, the
read counterpart to closing one with `resolve`. `daimon decide` is its
human-side mirror: everything actually waiting on a person, each with the
exact command that closes it.

```sh
daimon decide
daimon decide --all-projects
```

What qualifies is structural, not a matter of taste: a record belongs on
this queue only when its own write path refuses a non-human channel, the
same rule that makes `request accept`, `amend ratify`, `ruling ratify`, and
`refute ratify` human-only in the first place. `decide` reads records that
already exist and writes nothing at all, so opening it never changes what an
agent sees waiting. It is scoped to this project by default; other projects
arrive as counts only, and `--all-projects` expands those into full text,
each command pre-routed with `--slug` so it still runs from where you are.
The briefing carries the same count on one line, pointing back here.

## Amendments: state moved, the loop is still open

An amendment says a briefed item's state advanced while the item itself
stays open. Not a resolution (the loop has not closed) and not a reverify
(nothing about it went stale): it is the verb for the middle, evidence-
carrying step, such as "the referenced issue got approved" or "the blocker
cleared."

```sh
daimon amend propose <item-id> --change progressed \
  --evidence "the PR merged" --by agent
daimon amend ratify a-0f1e2d3c4b5a
daimon amend reject a-0f1e2d3c4b5a --note "the PR merged but the item covers a different repo"
daimon amend list
```

`--change` is a closed set: `progressed`, `blocked`, or `changed`. The
`--evidence` is a verbatim transcript quote, not a summary, byte-checked
against the session's own transcript at session end. An agent's proposal
stays invisible in the briefing until that byte-check passes; once it does,
it renders as a flagged, agent-attributed, unconfirmed line with its own
confirm and reject commands, never as settled fact, because a passed
byte-check certifies that the quote exists, not that it means what the
agent says it means. Only an explicit human `ratify` earns the plain
"amended" frame. A rejected amendment can be proposed again: rejection is a
recorded outcome, not a lock on the claim.

## `log`: a freeform trail

```sh
daimon log --text "…" --kind note
```

`log` appends one freeform timeline event to the project's own audit trail.
Zero-LLM, nothing extracted, nothing ranked: it is for the note that belongs
in the record but is not itself a loop, a decision, or a claim about the
present state of anything.
