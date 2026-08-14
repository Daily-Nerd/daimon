# Relations (typed, human-confirmed)

The relation ledger records typed claims between memory items — "this decision
revises that one", "this answers that question" — as an append-only record in
shadow mode: relations exist alongside your memory and never change what any
other surface does on their own.

The authority boundary is the whole design: **machines may propose, and none
may confirm.** A verdict requires an interactive terminal, and there is no
flag that delegates it to an agent. Candidates never render on an entry's
surface — the adjudication list is the one place they are visible. A chain a
reader sees in the viewer's History panel is one a person vouched for.

## Verbs

| command | what it does |
| --- | --- |
| `daimon relations list` | Every renderable relation, candidates first; endpoint texts resolved at read time. `--state` filters; `--json` for rows. |
| `daimon relations show <rel-id>` | One relation with its full proposal history. |
| `daimon relations confirm <rel-id>` | Record a human confirmation of a candidate edge. Human-only: needs an interactive terminal. |
| `daimon relations reject <rel-id>` | Record a human rejection — sticky against re-proposal. Human-only. |
| `daimon relations retract <rel-id>` | Undo a confirmation; a fresh proposal may revive the edge. Human-only. |

## Deletion contract

An edge is itself a claim about content, so relations honor `daimon forget`:
an edge touching a forgotten item is withheld from rendered surfaces, and only
a count of withheld edges is shown — the wording on the CLI and in the viewer
is the same, and it names no id. An endpoint that merely aged out of the
checkpoint retention window is different: it renders as `[unresolved]` and
stays a valid record.

## Adjudication advice

Read a chain whole before confirming its edges. Two items can look related
pairwise because they share project vocabulary while being different thoughts —
the honest verdicts are "this is the same thought evolving" (confirm),
"this edge is wrong" (reject), or leaving the candidate alone when you are
not sure. An unconfirmed candidate is inert; a wrong confirmation is not.
