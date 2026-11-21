---
sidebar_position: 2
description: "Cada comando `daimon` del grupo Preparar: uso, argumentos y valores por defecto, generado desde la CLI."
---

# Preparar

Instalar las piezas de daimon en una máquina, llenar el primer briefing y servir las herramientas.

:::note
Esta página se genera a partir de los parsers de argumentos de la CLI. No la edites: cambiá el flag o su texto de ayuda en el código y corré `UPDATE_DOCS_REFERENCE=1 uv run pytest tests/test_docs_generated.py` desde `plugin/`. El texto de cada flag está en inglés porque es el que imprime la CLI.
:::

Ver también: [Importar sesiones pasadas](../../guides/import-past-sessions.md), [Actualizar daimon](../../guides/upgrade.md), [Resolución de problemas](../../guides/troubleshooting.md), [Configuración](../../reference/configuration.md), [Servidor MCP](../../reference/mcp.md).

## `daimon configure`

detect the resolved LLM backend and fill gaps in ~/.daimon/env

```text
daimon configure [--backend litellm|command|claude-cli] [--api-key API_KEY] [--model MODEL] [--base-url BASE_URL] [--command COMMAND] [--output OUTPUT] [--input INPUT] [--init] [--timeout TIMEOUT] [--author AUTHOR] [--team-remote TEAM_REMOTE] [--test]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--backend` | `litellm\|command\|claude-cli` |  | non-interactive: pin this backend and write the value flags below |
| `--api-key` | `API_KEY` |  | litellm: DAIMON_LLM_API_KEY |
| `--model` | `MODEL` |  | litellm: DAIMON_LLM_MODEL |
| `--base-url` | `BASE_URL` |  | litellm: DAIMON_LLM_BASE_URL |
| `--command` | `COMMAND` |  | command: DAIMON_LLM_COMMAND |
| `--output` | `OUTPUT` |  | command: DAIMON_LLM_COMMAND_OUTPUT (text\|json:&lt;key&gt;) |
| `--input` | `INPUT` |  | command: DAIMON_LLM_COMMAND_INPUT (stdin\|arg\|file:&lt;flag&gt;): how the prompt reaches a CLI that doesn't read stdin, e.g. --input 'file:--prompt-file' for the Devin CLI (#58) |
| `--init` |  |  | guided setup wizard (#368): backend, timeout, immediate --test offer, optional team walk, ends with the status summary; every prompt has a flag escape hatch for scripts |
| `--timeout` | `TIMEOUT` |  | --init: DAIMON_TIMEOUT (total serialize budget, floor 420s) |
| `--author` | `AUTHOR` |  | --init: DAIMON_AUTHOR for team memory |
| `--team-remote` | `TEAM_REMOTE` |  | --init: git remote URL: sets DAIMON_TEAM=1 and runs `daimon team init <url>` |
| `--test` |  |  | send one tiny prompt through the resolved backend and report pass/fail: run this right after configuring (#56) |

## `daimon hooks`

ship host hook scripts from the package (#43): list, install, status

```text
daimon hooks <command>
```

| comando | qué hace |
| --- | --- |
| [`list`](#daimon-hooks-list) | hosts with packaged hook scripts |
| [`status`](#daimon-hooks-status) | audit installed hook copies against the packaged versions (CURRENT/STALE/MISSING/NOT INSTALLED), and this project's check manifest against its ledger; non-zero exit on either drift. --json appends one trailing `checks-manifest` entry carrying the manifest audit, after the real hosts |
| [`install`](#daimon-hooks-install) | copy a host's hook script(s) to the stable path ~/.daimon/hooks/ and print the registration snippet: re-run after every upgrade |
| [`remove`](#daimon-hooks-remove) | unregister daimon's hook entries from a host whose registration daimon wrote (kimi, codex). The installed hook scripts are left in place, inert once unregistered; the MCP wrapper is removed along with its registration |

### `daimon hooks list`

hosts with packaged hook scripts

```text
daimon hooks list
```

### `daimon hooks status`

audit installed hook copies against the packaged versions (CURRENT/STALE/MISSING/NOT INSTALLED), and this project's check manifest against its ledger; non-zero exit on either drift. --json appends one trailing `checks-manifest` entry carrying the manifest audit, after the real hosts

```text
daimon hooks status [--json]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |

### `daimon hooks install`

copy a host's hook script(s) to the stable path ~/.daimon/hooks/ and print the registration snippet: re-run after every upgrade

```text
daimon hooks install [[host]] [--all]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[host]` |  |  | host to install (see `daimon hooks list`); omit to detect the hosts on this machine |
| `--all` |  |  | install for every detected host without asking (unattended provisioning) |

### `daimon hooks remove`

unregister daimon's hook entries from a host whose registration daimon wrote (kimi, codex). The installed hook scripts are left in place, inert once unregistered; the MCP wrapper is removed along with its registration

```text
daimon hooks remove [[host]] [--all]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[host]` |  |  | host to unregister |
| `--all` |  |  | unregister every detected host daimon registered |

## `daimon skill`

install the daimon agent skill into a host's rules/skills file (#66)

```text
daimon skill <command>
```

| comando | qué hace |
| --- | --- |
| [`list`](#daimon-skill-list) | hosts with a skill renderer |
| [`show`](#daimon-skill-show) | print the canonical skill content |
| [`status`](#daimon-skill-status) | audit each installed skill against what this CLI would write now (CURRENT/STALE/MISSING/BROKEN/NOT INSTALLED); non-zero exit on drift. --json for machines |
| [`install`](#daimon-skill-install) | write the skill for a host (global scope by default); with no host, detect what this machine runs |
| [`uninstall`](#daimon-skill-uninstall) | remove exactly what install wrote |

### `daimon skill list`

hosts with a skill renderer

```text
daimon skill list
```

### `daimon skill show`

print the canonical skill content

```text
daimon skill show [--compact]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--compact` |  |  | print the rules-host variant instead of SKILL.md |

### `daimon skill status`

audit each installed skill against what this CLI would write now (CURRENT/STALE/MISSING/BROKEN/NOT INSTALLED); non-zero exit on drift. --json for machines

```text
daimon skill status [--json]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--json` |  |  | machine-readable output |

### `daimon skill install`

write the skill for a host (global scope by default); with no host, detect what this machine runs

```text
daimon skill install [[host]] [--project] [--all]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[host]` |  |  | host to install (see `daimon skill list`); omit to detect the hosts on this machine |
| `--project` |  |  | write into the current repo instead of $HOME |
| `--all` |  |  | install for every detected host without asking (unattended provisioning) |

### `daimon skill uninstall`

remove exactly what install wrote

```text
daimon skill uninstall [[host]] [--project] [--all]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `[host]` |  |  |  |
| `--project` |  |  |  |
| `--all` |  |  | remove from every detected host daimon serves |

## `daimon check`

armed rulings' checks (#943): rebuild what a host hook reads

```text
daimon check <command>
```

| comando | qué hace |
| --- | --- |
| [`sync`](#daimon-check-sync) | rebuild the check manifest and bodies from this project's ledger; safe to repeat, and every ratify already does it |

### `daimon check sync`

rebuild the check manifest and bodies from this project's ledger; safe to repeat, and every ratify already does it

```text
daimon check sync [--check] [--json] [--project PROJECT]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--check` |  |  | audit only: report whether the manifest matches this project's ledger and write nothing (0 in step, 1 drifted, 3 unreadable) |
| `--json` |  |  | machine-readable output |
| `--project` | `PROJECT` |  | project directory (default: DAIMON_PROJECT_DIR, then cwd) |

## `daimon heal`

re-serialize the most recent FAILED session if it can be done safely

```text
daimon heal [--dry-run] [--force] [--all] [--project PROJECT] [--session SESSION] [--dismiss SESSION_ID] [--presumed-dead-before EPOCH]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--dry-run` |  |  | explain what heal would repair (and why not) without serializing |
| `--force` |  |  | ignore a prior retry marker and re-heal a retry-exhausted session (#15) |
| `--all` |  |  | heal every healable session, one at a time, re-planning after each (default: only the most recent failure) |
| `--project` | `PROJECT` |  | heal only the failures of this project directory (runs every one of them, like --all) |
| `--session` | `SESSION` |  | heal only this session id (repeatable); a Codex rollout stem names its session |
| `--dismiss` | `SESSION_ID` |  | end an outstanding failure by hand (human only, stands alone): heal will not retry it, even if its transcript still exists |
| `--presumed-dead-before` | `EPOCH` |  | assert that no serialize of --project started before EPOCH is still alive, unless a stamp was touched after it (a host whose children die with it, such as a container; never on a laptop) |

## `daimon import`

serialize past transcripts of one project in one bounded run; prints the plan and about how many model calls it costs before spending

```text
daimon import [--project PROJECT] [--transcript PATH] [--sessions N] [--max-calls N] [--plan]
```

| argumento | valor | por defecto | descripción |
| --- | --- | --- | --- |
| `--project` | `PROJECT` |  | project directory the transcripts belong to (default: DAIMON_PROJECT_DIR, then cwd); a transcript that records no project (Windsurf, Kimi) is accepted only when this flag is given |
| `--transcript` | `PATH` |  | a transcript file to import (repeatable); one that records a working directory must record this project's. Without it the project's transcripts are discovered on disk (see --sessions) |
| `--sessions` | `N` |  | discover the project's N newest transcripts under the Claude Code and Codex roots (default: 5), newest by session end, matched on the working directory each file recorded; the skips the plan names come out of those N and nothing older takes their place, so the same command rebuilds the same plan. Windsurf and Kimi record no project: pass --transcript for those. Gemini (no registered root) and Hermes (no transcript file) cannot be imported yet, by discovery or by --transcript |
| `--max-calls` | `N` |  | stop before the session whose planned calls would take the spent count past N (default: the planned total); a run passes N only by one session's own retries and rescue |
| `--plan` |  |  | print the plan and exit without a model call |

## `daimon mcp`

opt-in read-only MCP server (#261): recall/brief/projects/status as a self-describing tool surface for MCP-capable hosts

```text
daimon mcp <command>
```

| comando | qué hace |
| --- | --- |
| [`serve`](#daimon-mcp-serve) | serve MCP over stdio until EOF: reads only, never writes; register in your host's MCP config (see the docs site) |

### `daimon mcp serve`

serve MCP over stdio until EOF: reads only, never writes; register in your host's MCP config (see the docs site)

```text
daimon mcp serve
```
