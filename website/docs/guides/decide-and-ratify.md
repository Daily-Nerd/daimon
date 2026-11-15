---
sidebar_position: 3
description: "Work the decide queue: see what only a person can settle, close each entry with its command, and record that an item's state moved."
---

# Decide and ratify

Some changes can only be made by a person at a terminal: accepting a request, ratifying a ruling, a refutation or an amendment. `daimon decide` is the queue of exactly those, and every entry carries the command that closes it. Why the queue is limited to human-only verbs is in [requests and decisions](../concepts/requests-and-decisions.md#decide-what-is-waiting-on-you).

## See what is waiting

```sh
daimon decide
daimon decide --all-projects
```

`daimon decide` reads existing records and writes nothing. Other projects appear as counts. `--all-projects` expands them into full text, each command routed with `--slug=<slug>` so it still runs from where you are, and it is refused on a tenant-scoped home (`DAIMON_TENANT_SCOPED`).

## Close an entry

| waiting on you | say yes | say no |
| --- | --- | --- |
| a request addressed to you | `daimon request accept <id>` | `daimon request reject <id> --note "<why>"` |
| an amendment | `daimon amend ratify <id>` | `daimon amend reject <id> --note "<why>"` |
| a ruling candidate | `daimon ruling ratify <id>` | `daimon ruling retire <id>` |
| a refutation candidate | `daimon refute ratify <id>` | `daimon refute overturn <id>` |

Each of these needs an interactive terminal, and `--by agent` is refused. `daimon ruling ratify` shows the full rule text and asks for an explicit `y` before it activates anything.

## Record that an item moved on

An amendment says a briefed item's state advanced while the item stays open: "the PR merged" or "the blocker cleared". An agent proposes it, a person ratifies it.

```sh
daimon amend propose <item-id> --change progressed \
  --evidence "the PR merged" --by agent
daimon amend ratify a-7d41e0
daimon amend reject a-7d41e0 --note "the PR merged but the item covers a different repo"
daimon amend list
```

`--change` is `progressed`, `blocked` or `changed`. `--evidence` is a verbatim quote from the session transcript, which daimon byte-checks at session end. What the check does and does not certify is in [requests and decisions](../concepts/requests-and-decisions.md#amendments-state-moved-the-loop-is-still-open).
