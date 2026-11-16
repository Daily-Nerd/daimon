---
sidebar_position: 4
description: "Cada comando `daimon` del grupo Comprobar: uso, argumentos y valores por defecto, generado desde la CLI."
---

# Comprobar

Preguntar por qué se confía en un item y comprobar lo que el store promete.

:::note
Esta página se genera a partir de los parsers de argumentos de la CLI. No la edites: cambiá el flag o su texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` desde `plugin/`. El texto de cada flag está en inglés porque es el que imprime la CLI.
:::

Ver también: [Inspeccionar un item](../../guides/inspect-an-item.md), [Clases de confianza](../../concepts/trust-classes.md), [Recibos](../../concepts/receipts.md), [Visor](../../reference/viewer.md), [Relaciones](../../reference/relations.md).

## `daimon why`

inspect the evidence and lifecycle receipt for one item

```text
daimon why item_id [--source] [--json] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `item_id` |  |  | exact item id shown by `daimon recall` or `daimon loops` |
| `--source` |  |  | show one bounded, redacted message-level source window |
| `--json` |  |  | machine-readable evidence axes |
| `--project` | `PROJECT` |  | project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | scope to a project bucket by its slug (see `daimon projects`) |

**Ejemplos**

```text
daimon recall retry policy
daimon why o-3f8a2c
daimon why o-3f8a2c --source --json
```

## `daimon diff`

what changed between two retained checkpoints of this project (#975): added, restated, re-tagged, and every item the chain stopped carrying, with the reason the record gives

Compare two retained checkpoints of one project and report every item that changed. Read-only: it stamps nothing. Rollback is a non-goal: a checkpoint records what a session did, so restoring one would assert a different past; use forget, resolve or reverify to say so on the record instead.

```text
daimon diff [--from N] [--to M] [--json] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--from` | `N` |  | the OLDER endpoint, in generations back: 0 is latest.json, 1 is prev-1.json (default: the latest readable pair) |
| `--to` | `M` |  | the NEWER endpoint, same numbering; must be smaller than --from |
| `--json` |  |  | machine-readable change rows |
| `--project` | `PROJECT` |  | project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | scope to a project bucket by its slug (see `daimon projects`) |

**Ejemplos**

```text
daimon diff
daimon diff --from 2 --to 0
daimon diff --json
```

## `daimon blame`

how one item got here (#975): the session that first stated it, every carry since, and every event that changed its state, in order

Trace one item through this project's retained checkpoint chain. `daimon why` answers where a claim came from; this answers how it got here. Read-only, and there is no restore: a forgotten item keeps its tombstone here and never its text.

```text
daimon blame item_id [--json] [--project PROJECT] [--slug SLUG]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `item_id` |  |  | exact item id shown by `daimon recall`, `daimon loops` or `daimon diff` |
| `--json` |  |  | machine-readable lineage |
| `--project` | `PROJECT` |  | project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--slug` | `SLUG` |  | scope to a project bucket by its slug (see `daimon projects`) |

**Ejemplos**

```text
daimon blame o-3f8a2c
daimon blame o-3f8a2c --json
```

## `daimon verify-receipt`

verify a checkpoint's signed provenance receipt via the vitni CLI (#204)

```text
daimon verify-receipt [[session_id]] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[session_id]` |  |  | session id to verify (default: this project's latest checkpoint) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

**Ejemplos**

```text
daimon verify-receipt
daimon verify-receipt <session-id>
```

## `daimon audit`

read-only auditors that verify stored guarantees

```text
daimon audit <command>
```

| comando | qué hace |
| --- | --- |
| [`quotes`](#daimon-audit-quotes) | re-check stored verbatim quotes against their source transcripts and report mismatches (read-only, never rewrites tags, #125; exit 0 checked clean, 1 mismatch, 3 nothing checkable) |
| [`privacy`](#daimon-audit-privacy) | prove no forgotten value's plaintext survives on any surface (read-only; exit 0 clean, 1 residue, 3 cannot-prove) |

### `daimon audit quotes`

re-check stored verbatim quotes against their source transcripts and report mismatches (read-only, never rewrites tags, #125; exit 0 checked clean, 1 mismatch, 3 nothing checkable)

```text
daimon audit quotes [--project PROJECT] [--all] [--top TOP] [--json]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--all` |  |  | audit every project's checkpoints, not just the current one |
| `--top` | `TOP` | `10` | how many failing quotes to list (default: 10) |
| `--json` |  |  | machine-readable output; lists EVERY failure, where the printed lines stop at --top |

**Ejemplos**

```text
daimon audit quotes
daimon audit quotes --all --top 20
daimon audit quotes --json
```

### `daimon audit privacy`

prove no forgotten value's plaintext survives on any surface (read-only; exit 0 clean, 1 residue, 3 cannot-prove)

```text
daimon audit privacy [--project PROJECT] [--all]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--all` |  |  | audit every local project, each against its own tombstone set |

**Ejemplos**

```text
daimon audit privacy
daimon audit privacy --all
```

## `daimon audit-quotes`

```text
daimon audit-quotes [--project PROJECT] [--all] [--top TOP]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  |  |
| `--all` |  |  |  |
| `--top` | `TOP` | `10` |  |

## `daimon serve`

serve the read-only local viewer (localhost only)

Serve the read-only local viewer on localhost. Every surface renders an existing engine's output; nothing writes.

```text
daimon serve [--data-dir DATA_DIR] [--project-dir PROJECT_DIR] [--port PORT] [--no-browser]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--data-dir` | `DATA_DIR` |  | checkpoint dir (default: DAIMON_CHECKPOINT_DIR, then ~/.daimon/checkpoints) |
| `--project-dir` | `PROJECT_DIR` |  | project directory to scope to (default: cwd) |
| `--port` | `PORT` |  | port to bind (default: 7717) |
| `--no-browser` |  |  | don't open a browser tab |

**Ejemplos**

```text
daimon serve
daimon serve --port 7800 --no-browser
```

## `daimon relations`

inspect and decide typed item relations (#678, shadow mode)

```text
daimon relations <command>
```

| comando | qué hace |
| --- | --- |
| [`list`](#daimon-relations-list) | candidates first; endpoint texts resolved at read time |
| [`show`](#daimon-relations-show) | one relation with its proposal history |
| [`confirm`](#daimon-relations-confirm) | record a human confirmation of a candidate edge (human-only: needs an interactive terminal) |
| [`reject`](#daimon-relations-reject) | record a human rejection; sticky against re-proposal (human-only: needs an interactive terminal) |
| [`retract`](#daimon-relations-retract) | undo a confirmation; a fresh proposal may revive it (human-only: needs an interactive terminal) |

**Ejemplos**

```text
daimon relations list
daimon relations show rel-0123456789abcdef
daimon relations confirm rel-0123456789abcdef
```

### `daimon relations list`

candidates first; endpoint texts resolved at read time

```text
daimon relations list [--state candidate|confirmed|rejected|retracted] [--project PROJECT] [--json]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--state` | `candidate\|confirmed\|rejected\|retracted` |  | filter by state; repeatable (default: all) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon relations show`

one relation with its proposal history

```text
daimon relations show relation_id [--project PROJECT] [--json]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `relation_id` |  |  | exact rel-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--json` |  |  | machine-readable output |

### `daimon relations confirm`

record a human confirmation of a candidate edge (human-only: needs an interactive terminal)

```text
daimon relations confirm relation_id [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `relation_id` |  |  | exact rel-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon relations reject`

record a human rejection; sticky against re-proposal (human-only: needs an interactive terminal)

```text
daimon relations reject relation_id [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `relation_id` |  |  | exact rel-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

### `daimon relations retract`

undo a confirmation; a fresh proposal may revive it (human-only: needs an interactive terminal)

```text
daimon relations retract relation_id [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `relation_id` |  |  | exact rel-… id |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
