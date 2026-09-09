---
description: "Set up daimon on Kimi Code. UserPromptSubmit-only briefing, throttled Stop capture as the print-mode path, and the pre-action checks not wired yet."
---

# Kimi Code

Kimi Code support is measured on one live 0.42.0 session (2026-09-09): the
install path, the three hook registrations, and the print-mode gap below all
come from that run, not from a fleet. Ruling pre-action checks are not wired
for this host yet.

## Install

Adds daimon's capture -> inject loop to Kimi Code from the released package:

```sh
daimon hooks install kimi
```

This copies three hook scripts, plus the modules they share, into the Kimi
config directory, then appends `[[hooks]]` entries to
`~/.kimi-code/config.toml` (or `$KIMI_CODE_HOME/config.toml` when that
variable is set). The config file is backed up before daimon writes to it.
The writer only ever appends or removes daimon's own blocks. It never
rewrites the provider or model tables.

Re-run `daimon hooks install kimi` after every `uv tool upgrade
daimon-briefing`, same as every other host, so the installed scripts match
the CLI version. Hooks load once, at session start: after installing, start
a new Kimi session. The one already running will not pick up the change.

Requires the `daimon` CLI on `PATH`:

```sh
uv tool install 'daimon-briefing[pretty]'
```

## What each script does

- **`daimon-kimi-user-prompt-submit.py`**: `UserPromptSubmit` hook. This is
  the only channel Kimi gives a hook to put text into the session, so it
  carries the briefing. Kimi has no session-start injection point (see
  Limitations below). The model sees the briefing as a user message wrapped
  in a `hook_result` tag, attached to your first prompt.
- **`daimon-kimi-session-end.py`**: `SessionEnd` hook. Serializes the
  finished session when an interactive Kimi session exits. It is
  idempotent: a session the `Stop` hook already captured is not written a
  second time.
- **`daimon-kimi-stop.py`**: `Stop` hook. Runs a throttled capture after
  each turn. This is crash insurance for an interactive session, and it is
  the only capture path in print mode, where `SessionEnd` never fires (see
  Limitations).

## Limitations

- **No briefing at session start.** Kimi's `SessionStart` hook is
  observation only, its stdout is dropped. The briefing arrives with your
  first prompt instead, through the `UserPromptSubmit` hook above.
- **Print mode never closes.** `kimi -p` sessions stay resumable and never
  fire `SessionEnd`, measured twice on a live session. The `Stop` hook is
  what captures them, and `SessionEnd`'s serialize stays idempotent, so a
  session already captured there is not written twice.
- **No pre-action checks yet.** Kimi's deny channel has not been measured on
  a live session, so this release ships no check profile for it.
- **Hooks load at session start only.** Installing does not reach a session
  already running. Start a new one to pick up the change.
- **macOS records the resolved path.** Kimi stores the working directory it
  resolves to, so a session started under `/tmp/x` is recorded as
  `/private/tmp/x`. Keep that in mind when you look sessions up by
  directory.

## Transcripts

Kimi writes each session's transcript to
`~/.kimi-code/sessions/<workspace>/<session id>/agents/main/wire.jsonl`. The
hook payload carries the session id, not a path, so daimon resolves the
transcript by globbing for that session id under the sessions directory.
Subagent transcripts are not merged into the main transcript in this
release.

## MCP

Kimi needs no daimon-specific MCP adapter. It reads a project-root
`.mcp.json` in the same format Claude Code uses, so an existing daimon MCP
entry works unchanged.

## Teach the agent the protocol

```sh
daimon skill install kimi              # ~/.kimi-code/skills/daimon/SKILL.md
daimon skill install kimi --project    # <repo>/.kimi-code/skills/daimon/SKILL.md
```

Kimi scans both locations and injects the skill's name and description at
session start, loading the body only when the skill is invoked. Project
scope wins over user scope. Re-run install after upgrading `daimon` to
refresh the content.

## Remove

```sh
daimon hooks remove kimi
```

This takes daimon's `[[hooks]]` entries back out of `config.toml` and leaves
everything else in the file byte for byte as it was. The installed scripts
stay where they are, inert once unregistered. Checkpoints under `~/.daimon/`
are untouched.

## Verify

```sh
daimon status
```

`daimon status` reports capture health for Kimi the same as any other host,
including the print-mode gap above. A capture made through the `Stop`
throttle counts the same as one made through `SessionEnd`.
