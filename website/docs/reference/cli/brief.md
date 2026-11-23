---
sidebar_position: 3
description: "Every `daimon` command in the Brief group: usage, arguments and defaults, generated from the CLI."
---

# Brief

Read where you left off.

:::note
This page is generated from the CLI's argument parsers. Do not edit it: change the flag or its help text in the code, then run `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` from `plugin/`.
:::

See also: [Carry](../../concepts/carry.md).

## `daimon brief`

render the briefing from the latest checkpoint

```text
daimon brief [--project PROJECT] [--team] [--slug SLUG] [--global-fallback] [--auto] [--label]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory to brief (default: DAIMON_PROJECT_DIR, then cwd) |
| `--team` |  |  | also show a 'Teammates' section: each teammate's active topic + recent decisions from the shared team memory (#111) |
| `--slug` | `SLUG` |  | brief another project's bucket by its slug (see `daimon projects`): deliberate cross-project read, provenance-labeled, no fallback (#243) |
| `--global-fallback` |  |  | when this project has no checkpoint, render the full global checkpoint (possibly another project's) instead of the header-only note (#96) |
| `--auto` |  |  | mark this render as hook-driven (SessionStart) so `daimon stats` can separate automatic briefings from deliberate re-reads (#54) |
| `--label` |  |  | print one `DAIMON BRIEFING (checkpoint: ...)` line above the briefing, naming the checkpoint this read rendered and whether it came off the global pointer; host hooks print it as given (#1223) |

**Examples**

```text
daimon brief
daimon brief --project .
DAIMON_PLAIN=1 daimon brief
```

## `daimon recall`

search local + team checkpoint history (FTS5)

```text
daimon recall query ... [--project PROJECT] [--all-projects] [--slug SLUG] [--json] [--limit LIMIT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `query` |  |  | search terms (matched as words against item text and quotes) |
| `--project` | `PROJECT` |  | project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--all-projects` |  |  | search across every project (lifts the project scope) |
| `--slug` | `SLUG` |  | scope to a project bucket by its slug (see `daimon projects`): reaches buckets whose source path no longer exists (#243) |
| `--json` |  |  | machine-readable output |
| `--limit` | `LIMIT` | `20` | max results (default: 20) |

**Examples**

```text
daimon recall auth caching
daimon recall gateway --all-projects --json
```

## `daimon handoff`

leave an authored baton for the next session: renders above everything in its next briefing (#523)

```text
daimon handoff [[text]] [--clear] [--by agent] [--project PROJECT]
```

| argument | value | default | description |
| --- | --- | --- | --- |
| `[text]` |  |  | the baton: imperative, small: what to do first and what to beware |
| `--clear` |  |  | retract the active baton |
| `--by` | `agent` |  | write the baton as agent text even at a terminal: it renders as written by an agent, unverified |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |
