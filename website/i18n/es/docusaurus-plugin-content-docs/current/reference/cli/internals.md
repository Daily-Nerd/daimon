---
sidebar_position: 9
description: "Cada comando `daimon` del grupo Internos: uso, argumentos y valores por defecto, generado desde la CLI."
---

# Internos

Los invocan los hooks; documentados por completitud.

:::note
Esta página se genera a partir de los parsers de argumentos de la CLI. No la edites: cambiá el flag o su texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` desde `plugin/`. El texto de cada flag está en inglés porque es el que imprime la CLI.
:::

## `daimon serialize`

serialize a transcript file into a checkpoint

```text
daimon serialize transcript [--project PROJECT] [--session SESSION] [--host HOST]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `transcript` |  |  | path to a text/markdown transcript |
| `--project` | `PROJECT` |  | project directory to route the checkpoint to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--session` | `SESSION` |  | session id for this transcript, overriding the filename (#988: a host whose transcript is not named for its session, such as Kimi Code, where every file is `wire.jsonl`) |
| `--host` | `HOST` |  | the name this host gives itself (#1314), for a host that runs serialize itself because no hook can: announces the run in serialize.log with its project, refuses (exit 75) while a live serialize for the session exists, records a crash in serialize-crash.log, and stamps DAIMON_CAPTURE_HOST unless set |

## `daimon write-checkpoint`

store a checkpoint read as JSON on stdin (introspection path, #23)

```text
daimon write-checkpoint [--project PROJECT] [--source SOURCE] [--session SESSION]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory to route the checkpoint to (default: DAIMON_PROJECT_DIR, then cwd) |
| `--source` | `SOURCE` | `introspection` | provenance stamp for the checkpoint (default: introspection) |
| `--session` | `SESSION` |  | the live session's REAL id (e.g. $CLAUDE_CODE_SESSION_ID), stamped onto the checkpoint in place of whatever session_id the JSON body carries (#983). This is what lets carry.py's same-session guard recognize a later reconstruction of THIS session as its own predecessor instead of an independent witness, so a provisional can never corroborate itself. Omit only when the host has no way to tell the live session its own id. |

## `daimon recall-inject`

proactive-suggestion backend for the UserPromptSubmit hook (#125): prompt on stdin, prints 0-2 prior-work lines, rc 0 always

```text
daimon recall-inject [--project PROJECT] [--session SESSION]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project dir for scoping (defaults to cwd detection) |
| `--session` | `SESSION` |  | current session id (excluded from matches; keys the cooldown) |

## `daimon action-recall`

action-keyed recall backend for the PreToolUse hook (#1031): shell command on stdin, prints 0-1 prior-work lines, rc 0 always

```text
daimon action-recall [--project PROJECT] [--session SESSION] [--record-only]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project dir for scoping (defaults to cwd detection) |
| `--session` | `SESSION` |  | current session id (excluded from matches; keys the cooldown) |
| `--record-only` |  |  | write the delivery-ledger row and print nothing (the soak rung of the per-host ladder) |

## `daimon request-inject`

live-delivery backend for the UserPromptSubmit hook (#756): prints undecided asks this session has not been shown, rc 0 always

```text
daimon request-inject [--project PROJECT] [--session SESSION]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project dir for scoping (defaults to cwd detection) |
| `--session` | `SESSION` |  | current session id (half the delivery write-once key) |
