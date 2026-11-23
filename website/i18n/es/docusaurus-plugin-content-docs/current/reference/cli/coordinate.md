---
sidebar_position: 7
description: "Cada comando `daimon` del grupo Coordinar: uso, argumentos y valores por defecto, generado desde la CLI."
---

# Coordinar

Pedirle algo a otro proyecto y responder lo que te piden.

:::note
Esta página se genera a partir de los parsers de argumentos de la CLI. No la edites: cambiá el flag o su texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` desde `plugin/`. El texto de cada flag está en inglés porque es el que imprime la CLI.
:::

Ver también: [Pedirle algo a otro proyecto](../../guides/ask-another-project.md), [Decidir y ratificar](../../guides/decide-and-ratify.md), [Pedidos y decisiones](../../concepts/requests-and-decisions.md).

## `daimon request`

ask another project for something, and answer what it asks (#694)

```text
daimon request <command>
```

| comando | qué hace |
| --- | --- |
| [`open`](#daimon-request-open) | open a request addressed to another project's slug |
| [`revise`](#daimon-request-revise) | answer a needs-info or sharpen an open ask; capped at 3 revisions per record |
| [`withdraw`](#daimon-request-withdraw) | take back a request this project sent; an agent may, because it is the sender's own ask and not a verdict. Final for that record: open a new one with --supersedes |
| [`severity`](#daimon-request-severity) | change how much a request you sent matters to you; an agent may. It moves no state and re-sends nothing: the new level shows on the recipient's next brief, inbox and decide. It is not --blocking |
| [`accept`](#daimon-request-accept) | accept an addressed request; a human decision, or an agent under a ruling that covers its sender |
| [`reject`](#daimon-request-reject) | reject it with a reason; rejection is final for that record, and the sender can open a new one citing it |
| [`needs-info`](#daimon-request-needs-info) | ask the sender for more before deciding |
| [`suppress`](#daimon-request-suppress) | drop it out of the briefing panel; it stays in `request list`, and any later verdict reverses it |
| [`done`](#daimon-request-done) | report the ask as satisfied; an agent claim renders as claimed-and-unverified until the byte-check |
| [`reply`](#daimon-request-reply) | send the sender a progress note on a request you accepted; it moves no state, and the request stays owed until `done` |
| [`list`](#daimon-request-list) | list this project's requests, undecided first |
| [`inbox`](#daimon-request-inbox) | requests addressed TO this project, from every sender, undecided first |

**Ejemplos**

```text
daimon request open --to ~/code/api --ask 'publish the 0.32 schema' --why 'the client cannot ship without it' --by agent
daimon request list
daimon request accept q-9c24d7
```

### `daimon request open`

open a request addressed to another project's slug

```text
daimon request open --to PROJECT --ask ASK --why WHY [--title TITLE] [--to-human] [--kind info|work] [--blocking] [--severity critical|high|normal|low] [--severity-reason REASON] [--evidence EVIDENCE] [--supersedes REQUEST_ID] [--anyway] [--by agent] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--to` | `PROJECT` |  | recipient project directory, or its slug written as --to=&lt;slug&gt; (a slug starts with '-', which a bare --to would read as an option); `daimon projects` lists both (obligatorio) |
| `--ask` | `ASK` |  | what you are asking that project to do (refused when it is too long to be shown whole to a person signing it) (obligatorio) |
| `--why` | `WHY` |  | why it matters; the recipient reads this first (obligatorio) |
| `--title` | `TITLE` |  | optional headline of a few words (80 characters at most) that leads wherever the request is listed; the ask stays the full text |
| `--to-human` |  |  | the ask is for that project's human, not its agent |
| `--kind` | `info\|work` | `work` | approval requirement: work (default) waits on a person, info is answerable from the recipient's own artifacts; only a human channel may open one as info |
| `--blocking` |  |  | mark the sender as blocked on it; a flag on the record, never a claim on the recipient's time |
| `--severity` | `critical\|high\|normal\|low` | `normal` | how much the outcome matters to you, which is not --blocking (whether you are stalled): the recipient's lists sort by it. Any level but the default needs --severity-reason |
| `--severity-reason` | `REASON` |  | one line, 80 characters at most (refused past that, never cut), shown with the level; required with every level but the default |
| `--evidence` | `EVIDENCE` |  | optional source backing the ask; recorded, never resolved |
| `--supersedes` | `REQUEST_ID` |  | the earlier q-… this ask replaces; the lineage renders always |
| `--anyway` |  |  | record the ask even when the recipient slug has no bucket yet; it is never surfaced there until that project serializes |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal, which is the human path |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request revise`

answer a needs-info or sharpen an open ask; capped at 3 revisions per record

```text
daimon request revise request_id [--ask ASK] [--why WHY] [--evidence EVIDENCE] [--title TITLE] [--by agent] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--ask` | `ASK` |  | replacement ask (same size rule as --ask) |
| `--why` | `WHY` |  | replacement rationale |
| `--evidence` | `EVIDENCE` |  | replacement source |
| `--title` | `TITLE` |  | replacement title; --title '' removes it, and leaving it out keeps the current one |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request withdraw`

take back a request this project sent; an agent may, because it is the sender's own ask and not a verdict. Final for that record: open a new one with --supersedes

```text
daimon request withdraw request_id --reason REASON [--by agent] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--reason` | `REASON` |  | why the ask no longer stands; the recipient reads it (obligatorio) |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request severity`

change how much a request you sent matters to you; an agent may. It moves no state and re-sends nothing: the new level shows on the recipient's next brief, inbox and decide. It is not --blocking

```text
daimon request severity request_id critical|high|normal|low [--reason REASON] [--by agent] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `critical|high|normal|low` |  |  | the new level; the default level clears a raised one |
| `--reason` | `REASON` |  | one line, 80 characters at most, shown with the level; required with every level but the default |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request accept`

accept an addressed request; a human decision, or an agent under a ruling that covers its sender

```text
daimon request accept request_id [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--note` | `NOTE` |  | kept on the record |
| `--by` | `agent` |  | declare yourself an agent; permitted only for an addressed ask (`info` or `work`) an active ruling of this project covers for its sender; otherwise it needs `daimon request accept` from a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon request reject`

reject it with a reason; rejection is final for that record, and the sender can open a new one citing it

```text
daimon request reject request_id [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--note` | `NOTE` |  | kept on the record |
| `--by` | `agent` |  | declare yourself an agent; the call then refuses, because this verb requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon request needs-info`

ask the sender for more before deciding

```text
daimon request needs-info request_id [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--note` | `NOTE` |  | kept on the record |
| `--by` | `agent` |  | declare yourself an agent; the call then refuses, because this verb requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon request suppress`

drop it out of the briefing panel; it stays in `request list`, and any later verdict reverses it

```text
daimon request suppress request_id [--note NOTE] [--by agent] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--note` | `NOTE` |  | kept on the record |
| `--by` | `agent` |  | declare yourself an agent; the call then refuses, because this verb requires a human channel |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | route to another project's bucket by its slug, written --slug=&lt;slug&gt; (a slug starts with '-'); routing only, the channel gate is unchanged (#766) |

### `daimon request done`

report the ask as satisfied; an agent claim renders as claimed-and-unverified until the byte-check

An agent may run this on an accepted ask without asking the human.

```text
daimon request done request_id --evidence EVIDENCE [--by agent] [--json] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--evidence` | `EVIDENCE` |  | what settles it; a verbatim quote from an agent is byte-checked at session end (obligatorio) |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal |
| `--json` |  |  | print &#123;request_id, event, event_id&#125; instead of the card |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request reply`

send the sender a progress note on a request you accepted; it moves no state, and the request stays owed until `done`

```text
daimon request reply request_id --note NOTE [--evidence EVIDENCE] [--by agent] [--json] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `request_id` |  |  | exact q-… id |
| `--note` | `NOTE` |  | the update the sender reads; 2000 characters (obligatorio) |
| `--evidence` | `EVIDENCE` |  | optional pointer to what backs the update |
| `--by` | `agent` |  | declare yourself an agent; omit it only from an interactive terminal. An agent reply reaches the sender labeled unverified |
| `--json` |  |  | print &#123;request_id, event, event_id&#125; instead of the card |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request list`

list this project's requests, undecided first

```text
daimon request list [--json] [--min-severity critical|high|normal|low] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |
| `--min-severity` | `critical\|high\|normal\|low` |  | only requests at this severity or a more severe one |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon request inbox`

requests addressed TO this project, from every sender, undecided first

```text
daimon request inbox [--json] [--min-severity critical|high|normal|low] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |
| `--min-severity` | `critical\|high\|normal\|low` |  | only requests at this severity or a more severe one |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

## `daimon decide`

list what is waiting on YOU with the command that closes each (#766): the human-side mirror of daimon loops

```text
daimon decide [--project PROJECT] [--all-projects]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--all-projects` |  |  | also list every other project's queue as text, composed per project, each command routed with --slug=&lt;slug&gt; so it runs from here; refused on a tenant-scoped home (#766, #899) |

**Ejemplos**

```text
daimon decide
daimon decide --project .
```
