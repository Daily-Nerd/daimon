---
sidebar_position: 5
description: "Standing rulings are human-ratified constraints that render into every future briefing and never decay. The lifecycle, the authority channels, the cap, checks with teeth, and layers."
---

# Standing rulings

Everything else on this site is memory: a claim about what happened, carried
forward until it decays or someone resolves it. A ruling is not a memory. It
is a constraint a human put in force, and it does not decay, does not
compete for carry, and does not get re-extracted by a model on the next
session. Once ratified, it renders in every briefing until a human retires
it.

Rulings live on the same append-only ledger as [refutations](./negative-knowledge.md):
same id space, same evidence citation, same deletion machinery. The two are
opposite polarities of one record. A refutation says what is not true.  A
ruling says what must hold from now on.

## The lifecycle

Four verbs move a ruling through its life:

```sh
daimon ruling propose --subject "public posts" --scope publishing \
  --evidence issue:693 --by agent
daimon ruling ratify r-1a2b3c4d5e6f
daimon ruling revise r-1a2b3c4d5e6f --evidence issue:700
daimon ruling retire r-1a2b3c4d5e6f
```

(`propose` and `revise` also take the flag that carries the rule text
itself; see the [CLI reference](../reference/cli.md) for the exact syntax.)

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

## Authority channels

Ratification is the one transition that must never be self-declarable. The
CLI can only ever observe two channels: an interactive terminal (`cli-tty`,
authority `human`) and an explicit `--by agent` (`cli-agent`, authority
`agent`). There is no flag for claiming to be human: the human channel is
the *absence* of `--by agent`, checked against a real terminal, not a string
an agent could pass. Two more channels, `ui` and `signed`, exist only for a
host process that holds verified operator authority itself and writes
through the library in process; no CLI flag reaches either one, because a
flag an agent could pass from a shell would just be `--by human` under
another name.

Every rendered ruling names its channel honestly: `ratified (interactive)`,
`ratified (ui)`, `ratified (signed)`, or, for a still-pending write,
`agent-proposed`. None of this makes forgery impossible. A caller with
machine access can still drive a UI or allocate a terminal. What the channel
buys is provenance, not proof: forgery costs deliberate impersonation
instead of one flag, and the record stays auditable afterwards.

One label survives ratification regardless of channel: if an agent authored
the rule text, the briefing marks it `[agent-written]` even once a human has
ratified it. Who wrote the words and who approved them are two different
facts, and the render keeps both visible.

## The cap, and why it is enforced where it is

A project can hold at most `DAIMON_RULING_CAP` active rulings, seven by
default. The number is a render-budget default, not a claim about how many
rules anyone actually needs. What matters is where the cap bites: at
**activation**, in `ratify` and in `propose --ratify`, never at render time.

The reasoning is blunt. The rulings section of a briefing always renders
what it has room for. If the cap were enforced at render instead, an eighth
active ruling would simply not print, silently, with nothing telling anyone
a human-ratified constraint had gone missing from the one place it is
supposed to always appear. That is the one failure this section is not
allowed to have. So the refusal happens earlier, at the write that would
create the eighth active ruling: ratify refuses with the ids already active,
you retire one or raise the cap on purpose. If a ledger somehow ends up over
cap anyway (a hand-edited file, or the cap lowered after the fact), the
briefing renders the cap's worth and adds a loud line naming how many are
being withheld, pointing at `daimon ruling list --inherited` for the full
picture.

## Checks with teeth

A ruling can carry a `check`: a shell script matched against a command
string, run before that command executes on a host that supports it.

```sh
daimon ruling propose --subject "commit messages" --scope git \
  --evidence issue:900 \
  --check-body-file check.sh --check-match '^git commit' \
  --check-intent enforce
```

The author asks for an *intent* (`enforce`, `warn`, or `record-only`); the
host delivers a *mode*, which is always the weaker of what was asked and
what that host can actually do. Claude Code delivers `enforce` and `warn` as
asked. Codex has no channel for a warning at all, so `warn` degrades to
`record-only` there. Windsurf documents no measured enforcement channel, so
every intent lands `unsupported` on it. `enforce` blocks the command with
the host's own structured deny; `warn` lets it through and shows the reason;
`record-only` says nothing and just logs that the check ran.

The check body itself travels with the ruling: its bytes are stored on the
record, never a path to a file, because a path is invisible on every other
machine the ruling might reach. `ratify` shows the check's exact body and
pins the activation to its hash, the same content-binding discipline the
rule text gets. `daimon ruling check try <id> --command "<cmd>"` runs a
check against a command you name without arming anything or logging
anything, which is the right way to rehearse one before you ratify it.
`daimon check sync` and `daimon hooks install` keep the on-disk manifest a
host reads in step with the ledger; `daimon ruling checks` shows what is
armed, on which host, and whether it has ever fired. The full mechanics,
including exactly what a check receives as its subject and every failure
mode, are in the [CLI reference](../reference/cli.md#checks-at-runtime).

## Request policies are a ruling class too

A ruling can grant a request-handling permission instead of, or alongside,
a check. Two shapes, chosen by `verb`:

- `sender=<slug> kind=work|info verb=accept by=agent` lets that sender
  project's own agent record `accept` on a `work` request this project owes
  it, without a person in the loop for that specific sender.
- `to=<slug> kind=info verb=open by=agent` lets this project's own agent
  open an `info`-kind ask toward that recipient, again with no person
  touching it.

Both render as a single compact line in the briefing rather than the usual
prose form, tagged so a reader can tell a code-enforced permission apart
from a stated rule. See [requests and decisions](./requests-and-decisions.md)
for what `kind` and the accept/open verbs actually do.

## Layer rulings

A ruling does not have to belong to one repository. Ratify it against a
plain directory above your projects, one that is not itself a git working
tree, and every project underneath inherits it:

```sh
daimon ruling propose --project ~/work --subject "shared secrets" \
  --scope "every repo under ~/work" --evidence issue:1092 --ratify
```

The directory qualifies as a layer once it sits at or below your home
directory, is not shadowed by a `.git` anywhere at or above it, and has its
own bucket. Every project under it then renders that ruling *before* its own,
tagged `[from ~/work]` so nobody mistakes an inherited rule for a local one.

Inheritance has teeth in both directions. An inherited ruling counts against
the child project's own cap, exactly as if it had been ratified locally,
because the failure the cap exists to prevent (a silently truncated section)
does not care whether the excess came from this project or from a layer
above it. And a child cannot quietly override or reclaim an inherited id:
`retire`, `revise`, and `ratify` on an id that only exists as a layer's
ruling all refuse in the child, and name the layer directory to run the
command from instead. A child also cannot found or activate its own ruling
under an id that a layer above it already holds active. When more rulings
are in force than a briefing has room to show, including inherited ones, the
over-cap note points at `daimon ruling list --inherited` for the merged
view.

See the [CLI reference](../reference/cli.md) for the full flag surface on
every verb above.
