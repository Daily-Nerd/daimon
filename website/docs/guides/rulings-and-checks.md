---
sidebar_position: 5
description: "Propose, ratify, revise and retire a standing ruling, attach a check to one, rehearse it before it is armed, and ratify at a layer above your repositories."
---

# Rulings and checks

A standing ruling is a rule a person put in force: it renders in every briefing and never decays. This guide covers the verbs, a check that runs before a shell command, and a ruling that binds every project under a directory. What a ruling is, and why only a person can ratify one, is in [standing rulings](../concepts/rulings.md).

## The four verbs


Four verbs move a ruling through its life:

```sh
daimon ruling propose --subject "public posts" --scope publishing \
  --evidence issue:693 --by agent
daimon ruling ratify r-5b19c3
daimon ruling revise r-5b19c3 --evidence issue:700
daimon ruling retire r-5b19c3
```

(`propose` and `revise` also take the flag that carries the rule text
itself; see the [CLI reference](../reference/cli/index.md) for the exact syntax.)

The rule text is also what a person signs when they ratify, so it has to fit on
the signing screen: at most 2048 bytes of UTF-8 and at most 32 lines as the
signer shows them, on top of the 280 character limit. Every byte outside plain
ASCII is shown as four characters, so wide text such as Chinese or Japanese
reaches the line limit first. A rule that does not fit is refused when it is
proposed or revised, with its size and the limit it passed.

- **`propose`** founds a candidate. An agent proposal stays a candidate:
  nothing renders, nothing enforces, until a human acts on it.
- **`ratify`** activates it. This is the only verb that turns a candidate
  into a standing rule, and it is deliberately theatrical: it prints the
  full rule text, discloses what it will mean for every future session, and
  asks for an explicit `y`. The append is bound to the exact text it just
  showed you, by content hash, so a race between the display and the
  confirm can never activate different words than the ones you read.
- **`revise`** changes the text, scope, or evidence. On an active ruling, an
  agent's revise writes a *proposal* and the ruling keeps its current text
  until a human accepts it with `ratify` (which shows the pending proposal,
  not the old text, before asking again). A human's revise on an active
  ruling applies immediately, no separate ratify needed for the edit itself.
- **`retire`** ends it. A human retires directly; an agent's retire is
  recorded as a proposal and the ruling stands. Evidence is optional here,
  because a rule that simply stopped applying often has no citation to give.
  The ledger records why the ruling ended: a candidate that never earned
  ratification reads **rejected**, an active ruling that stopped applying
  reads **retired**. Both end in the same state, `overturned`; the word is
  the label `ruling show` and `ruling list` print, so months later you can
  tell a proposal you turned down from a rule you used and then dropped.
  `daimon decide` lists only candidates and offers turning one down as
  `reject`; the command it gives is `ruling retire`. Rulings ended before
  this label existed keep reading `retired`.

## Attach a check

A ruling can carry a script that runs against a command before it executes, on a host that supports it.

```sh
daimon ruling propose --subject "commit messages" --scope git \
  --evidence issue:900 \
  --check-body-file check.sh --check-match '^git commit' \
  --check-intent enforce
```

1. Rehearse it: `daimon ruling check try <id> --command "<cmd>"` runs the check against a command you name, arms nothing and logs nothing.
2. Arm it with the lowest intent first. Start with `--check-intent warn`, so a pattern that matches more than you meant costs a warning and not a blocked action.
3. Ratify. `ratify` shows the check's exact body and pins the activation to its hash.
4. See what is armed with `daimon ruling checks`. `daimon check sync` rebuilds the manifest a host reads, and every ratify, revise and retire already does it.

What each host delivers, what a check receives as its subject and every failure mode are in [layer rulings and checks at runtime](../reference/check-runtime.md).

## Ratify at a layer

A ruling ratified at a plain directory above your repositories binds every project under it.

```sh
daimon ruling propose --project ~/work --subject "shared secrets" \
  --scope "every repo under ~/work" --evidence issue:1092 --ratify
```

A child project's briefing renders the inherited rulings first, tagged with the layer. `retire`, `revise` and `ratify` on an inherited id refuse inside the child and name the layer directory to run them from. `daimon ruling list --inherited` shows the merged view.
