---
sidebar_position: 1
description: "Every daimon command grouped by task, with flags and defaults generated from the CLI, plus the rules that apply across commands."
---

# CLI reference

Every daimon command, grouped by what you are trying to do. Each group page is generated from the CLI's argument parsers, so a flag or a default is described in one place: the code. `daimon <command> --help` prints the same facts.

| group | commands |
| --- | --- |
| [Set up](setup.md) | `configure`, `hooks`, `skill`, `check`, `heal`, `import`, `mcp` |
| [Brief](brief.md) | `brief`, `recall`, `handoff` |
| [Check](check.md) | `why`, `diff`, `blame`, `verify-receipt`, `audit`, `audit-quotes`, `serve`, `relations` |
| [Correct](correct.md) | `resolve`, `reverify`, `trust`, `anchor`, `refute`, `ruling`, `amend` |
| [Forget](forget.md) | `forget`, `unforget` |
| [Coordinate](coordinate.md) | `request`, `decide` |
| [Status](status.md) | `status`, `stats`, `log`, `loops`, `projects`, `slug`, `bucket`, `ledger`, `team` |
| [Internals](internals.md) | `serialize`, `write-checkpoint`, `recall-inject`, `action-recall`, `request-inject` |

On a tenant-scoped home (`DAIMON_TENANT_SCOPED`) no verb lets the caller leave
its own project: `--slug`, `--all-projects`, `audit privacy --all`,
`brief --global-fallback` and a `--project` that names another bucket are
refused with a plain error, and `decide` shows no counts of other projects.
Off tenant scope the verbs behave as described in the [command notes](notes.md). A shell can still change
its own directory or environment, so the CLI is not a boundary against an
agent that has one; the MCP tools and the library are.

## Stability

daimon is pre-1.0. A release that breaks a command, a flag or an output you may script against arrives as a minor version bump, so the version number alone will not warn you. Every such change appears under `### ⚠ BREAKING CHANGES` in `CHANGELOG.md`, with prose saying what moved and what to do about it. Pin an exact version and read that section before you move. For the library calls a host process can rely on, see the [Python API](../python-api.md).

## Exit codes of the auditors

The auditors share one exit contract, so a script can act on the answer:

| exit | meaning |
| --- | --- |
| `0` | proven clean, every surface was scanned and nothing was found |
| `1` | residue found; the report names the surface and the hash (never the text) |
| `3` | cannot prove, a surface could not be read, or nothing was in scope to scan. Never treat this as clean |

`--project <dir>` scopes to one project, `--all` audits every local project
(each against its own tombstones); the two are mutually exclusive.

## Behavior notes

The generated pages say what each flag is. What a command does beyond that (refusals, exit codes, what it writes, how the panels behave) is in the [command notes](notes.md) and in the guides and concept pages linked from each group.
