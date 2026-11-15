---
sidebar_position: 6
description: "Cada comando `daimon` del grupo Olvidar: uso, argumentos y valores por defecto, generado desde la CLI."
---

# Olvidar

Quitar el contenido de un item y levantar un olvido.

:::note
Esta página se genera a partir de los parsers de argumentos de la CLI. No la edites: cambiá el flag o su texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` desde `plugin/`. El texto de cada flag está en inglés porque es el que imprime la CLI.
:::

Ver también: [Ciclo de vida](../../concepts/lifecycle.md).

## `daimon forget`

remove ONE item from the live checkpoint and record a signed-state tombstone: content leaves disk and index; the event carries only a hash (#321)

```text
daimon forget [[target]] [--reason REASON] [--project PROJECT] [--dry-run] [--resume] [--only publish|value|id|wholesale|derived] [--republish]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[target]` |  |  | item id (exact) or a query that must match exactly one item |
| `--reason` | `REASON` |  | recorded on the tombstone event (redacted like any note) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | show what would be forgotten without writing: look before a destructive op |
| `--resume` |  |  | finish every phase for every forget already recorded: the cure for a surface that could not be reached; a person at a terminal, takes no target |
| `--only` | `publish\|value\|id\|wholesale\|derived` |  | with --resume, run only this phase |
| `--republish` |  |  | publish every local forget the team sidecar is missing (the cure for a publish that failed); takes no target; the same as --resume --only publish, and it goes in the next release |

**Ejemplos**

```text
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

## `daimon unforget`

lift a forget on this machine, from a terminal: the value is not restored, a new capture is admitted again

```text
daimon unforget target [--reason REASON] [--project PROJECT] [--dry-run]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `target` |  |  | the item id the forget receipt printed (exact only) |
| `--reason` | `REASON` |  | recorded on the lift rows (redacted like any note) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | count the refs that would be lifted without writing |

**Ejemplos**

```text
daimon unforget o-3f8a2c --reason "forgot the wrong one"
daimon unforget o-3f8a2c --dry-run
```
