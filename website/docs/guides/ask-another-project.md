---
sidebar_position: 2
description: "Open a request to another project, follow it, and answer one addressed to you: revise, withdraw, severity, accept, done and reply."
---

# Ask another project for something

Use this when work in one project depends on another. The request lives in your project's own bucket, and the other project answers in its own. The model behind it is in [requests and decisions](../concepts/requests-and-decisions.md).

## Open a request

```sh
daimon request open --to <dir> --ask "…" --why "…" --title "…"
```

`--to` takes the recipient's project directory, validated against the
projects daimon already knows about, with a suggestion on a likely typo.
`--title` is optional: a few words, 80 characters at most, naming what the
request is about. It leads the request wherever it is listed, and the ask
keeps the full text. Without one, the first sentence of the ask stands in.

The ask is also the text a person signs when they accept, reject or finish the
request, so it has to fit on the signing screen: at most 2048 bytes of UTF-8
and at most 32 lines as the signer shows them. A line the screen cannot hold
continues on the next, and every byte outside plain ASCII (an accented letter
is two) is shown as four characters, so text in other languages reaches the
limit sooner. A longer ask is refused when it is opened or revised, with its
size and the limit it passed. The other fields keep their 2000 character limit.

`--severity` says how much the outcome matters to you, on a fixed scale:
`critical`, `high`, `normal` and `low`. `normal` is the default and shows
nothing. Any other level needs `--severity-reason`, one line of 80 characters
at most (longer is refused, not cut), and it is shown with the level:
`High: the release waits on this`. It is not `--blocking`: severity is
how much it matters, `--blocking` is that you are stalled. The recipient's
decision and owed panels, `request inbox`, `request list` and `daimon decide`
order by it, keeping the age order inside a level, and the panels still show at
most three rows: severity changes which three. `request inbox --min-severity
high` (and the MCP `min_severity` argument) shows that level and anything
more severe. Either channel may set it, because it lowers no approval bar. A
request that replaces another with `--supersedes` carries no severity over.

After the ask is recorded, `request open` can print one more note. It appears
when nothing has been saved or shown in the recipient's bucket for 14 days or
more before a bucket next to it was active: both folders sit under the same
parent and have prefix-related names, such as `Acme` and `AcmeV2`. A bucket
counts as seen when a session saved a checkpoint there or was shown a request
by the brief or the prompt hook. Decisions made from that folder do not count.
The note gives both slugs and dates and asks whether they are one project. If
they are, ask the owner to run `daimon bucket merge`, which is what makes asks
reach one inbox. If they are not, ignore it. It never blocks, the ask is
written exactly as before, and it can be wrong when two separate repos happen
to have similar names. Buckets with no recorded folder, pairs already merged and
other tenants' buckets are never considered.

## As the sender

- **`revise`** answers a needs-info reply, or sharpens the ask, up to three
  times per record; a fourth attempt opens a new request with
  `--supersedes` instead, so the lineage stays visible rather than silently
  rewritten.
- **`withdraw`** is how a sender takes its own ask back: `daimon request
  withdraw <id> --reason "<why>"`, from either channel, because retracting
  your own ask is the sender's act and not the recipient's decision. It works on an open,
  needs-info, accepted or claim-pending ask and ends it as `withdrawn`, with
  the reason in `note`; no decision field is set. A done, rejected or
  already withdrawn request is left as it is, and the command says why. It
  is final for that record: ask again with `--supersedes`. A recipient that
  had already accepted the ask is told once, in one line, through live
  delivery. Only the sender can withdraw, and a sender can no longer
  `reject`, `accept`, `needs-info`, `suppress` or `done` its own ask: each
  request event belongs to one role, and a row written from the other
  side is inert.
- **`severity`** changes it later: `daimon request severity <id> <level>
  --reason "<why>"`, from either channel, while the request is open,
  needs-info or accepted (`normal` clears it). It is its own event and not a
  revision: it moves no state, uses none of the three revisions, discards no
  completion claim and re-sends nothing. The new level shows on the
  recipient's next brief, in `inbox` and in `daimon decide`. Only the sender
  can change it, and a request that is done, rejected or withdrawn keeps the
  level it had.

## As the recipient

- **`accept` / `reject` / `needs-info`** are the recipient's decision, taken in an interactive terminal. When an agent may accept without a person, and why, is in [Who decides](../concepts/requests-and-decisions.md#who-decides). Rejection is final for that record: the sender opens a new request rather than asking again.
- **`suppress`** drops a request out of the recipient's own briefing panel,
  attention only, human-only; the record stays fully visible in `list` and
  `inbox`, and any later decision reverses the suppression.
- **`done`** reports the ask as satisfied, from either channel (human or agent), by the project the request was sent to. An agent's
  claim renders as unverified until the recipient's next session-end
  byte-checks the evidence quote against its own transcript; a human `done`
  renders as stated. On a `work` request nobody has accepted yet, an
  agent's `done` records the claim but leaves the decision queue open,
  waiting for `accept` or `reject`.
- **`reply`** sends the sender a progress note on a request you accepted
  and have not finished. It moves no state, so the request stays owed until
  `done`. An agent's note reaches the sender labeled as an unverified agent
  claim. Any other state refuses it and names the verb that fits. A late
  reply is shown once, even after the decision aged out of the panel.

## See what is open

```sh
daimon request list    # this project's own sent requests
daimon request inbox   # requests addressed TO this project
```

Both take `--json`. The briefing itself carries four small panels drawn from
the same data: the recipient's "requests waiting on you" and "requests you
accepted and still owe," and the sender's "decisions on requests you sent"
and "requests you sent that nobody is moving," each capped with a loud overflow line
rather than a silent drop. The [MCP server](../reference/mcp.md) exposes the
recipient's view read-only, as the `requests_inbox` tool; no request-writing
verb is reachable over MCP, and `daimon_brief` itself never carries request
content.
