---
sidebar_position: 6
description: "Every `daimon` command in the Forget group: usage, arguments and defaults, generated from the CLI."
---

# Forget

Remove an item's content and lift a forget.

:::note
This page is generated from the CLI's argument parsers. Do not edit it: change the flag or its help text in the code, then run `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` from `plugin/`.
:::

See also: [Lifecycle](../../concepts/lifecycle.md).

## `daimon forget`

remove ONE item from the live checkpoint and record a signed-state tombstone: content leaves disk and index; the event carries only a hash (#321)

```text
daimon forget [[target]] [--reason REASON] [--project PROJECT] [--dry-run] [--resume] [--only publish|value|id|wholesale|derived] [--republish]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `[target]` |  |  | item id (exact) or a query that must match exactly one item |
| `--reason` | `REASON` |  | recorded on the tombstone event (redacted like any note) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | show what would be forgotten without writing: look before a destructive op |
| `--resume` |  |  | finish every phase for every forget already recorded: the cure for a surface that could not be reached; a person at a terminal, takes no target |
| `--only` | `publish\|value\|id\|wholesale\|derived` |  | with --resume, run only this phase |
| `--republish` |  |  | publish every local forget the team sidecar is missing (the cure for a publish that failed); takes no target; the same as --resume --only publish, and it goes in the next release |

**Examples**

```text
daimon forget o-3f8a2c --reason "contains client name"
daimon forget "wrong belief about retry nonce" --dry-run
```

## `daimon unforget`

lift a forget on this machine, from a terminal: the value is not restored, a new capture is admitted again

```text
daimon unforget target [--reason REASON] [--project PROJECT] [--dry-run]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `target` |  |  | the item id the forget receipt printed (exact only) |
| `--reason` | `REASON` |  | recorded on the lift rows (redacted like any note) |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
| `--dry-run` |  |  | count the refs that would be lifted without writing |

**Examples**

```text
daimon unforget o-3f8a2c --reason "forgot the wrong one"
daimon unforget o-3f8a2c --dry-run
```
