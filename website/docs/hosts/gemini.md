---
description: "Set up daimon on Gemini CLI. Briefing injection ships today; the upstream transcript_path stub (gemini-cli#14715) was fixed in gemini-cli v0.21.0, so serialize can run, but end-to-end capture is unverified by this project."
---

# Gemini CLI

Gemini support mirrors the Claude Code shape, split across two scripts. The
briefing hook is shipped. Serialize can run too: the upstream `transcript_path`
stub (`gemini-cli#14715`) was fixed in gemini-cli **v0.21.0** (2025-12-16), so
a real path reaches the `SessionEnd` hook on that version and later. What
`daimon serialize` does with a Gemini transcript once it has a real path is
unverified by this project, see the Verify section below.

## What each script does

- **`daimon-gemini-session-start.py`** — `SessionStart` hook. Shells out to
  `daimon brief` and injects the result via Gemini's
  `{"hookSpecificOutput": {"additionalContext": ...}}` envelope. Gemini
  requires **pure-JSON stdout** ("Silence is Mandatory") — unlike the Claude
  Code hook, nothing is ever printed raw; operator-facing diagnostics ride
  `{"systemMessage": ...}` instead. `SessionStart` is advisory-only: exit 0
  always, startup is never blocked.
- **`daimon-gemini-session-end.py`** — `SessionEnd` hook. Mirrors the Claude
  Code `SessionEnd` hook: spawns `daimon serialize <transcript_path>` detached
  when `transcript_path` is non-empty, and logs a graceful skip when it is
  not. On gemini-cli **v0.21.0 and later** (the fix for `gemini-cli#14715`),
  `transcript_path` carries a real path, so the hook spawns serialize. On
  earlier versions the field still arrives empty and the hook still skips.

## Install (manual, from a clone)

`gemini-hooks.py` is the lifecycle manager (same shape as `codex-hooks.py`):

```sh
python3 hook/gemini-hooks.py install   [--dry-run]
python3 hook/gemini-hooks.py uninstall [--dry-run]
python3 hook/gemini-hooks.py status
```

Install copies both scripts (plus `_daimon_hook_lib.py`) to `~/.gemini/hooks/`
and registers them in `~/.gemini/settings.json` (user layer). Requires the
`daimon` CLI on `PATH` (`uv tool install 'daimon-briefing[pretty]'`).

## MCP

`hook/gemini-hooks.py install` also registers the read-only
[MCP server](../reference/mcp) in `~/.gemini/settings.json`'s
`mcpServers.daimon`, alongside its two hooks, and removes it on
`uninstall`. Gemini has no per-prompt recall hook (SessionStart-only, see
above), so the recall hint has nowhere to render here yet.

## Teach the agent the protocol

```sh
daimon skill install gemini      # managed block in ~/.gemini/GEMINI.md
```

On the shared `GEMINI.md` file, daimon only ever touches its own marker
block — `daimon skill uninstall gemini` removes exactly that block. Re-run
install after upgrading `daimon` to refresh the content.

## Verify

```sh
daimon status
```

On gemini-cli **v0.21.0 or later**, `daimon status` should show a fresh
checkpoint for the project after a session ends. Whether a Gemini transcript
actually parses into a checkpoint is unverified by this project: `daimon
serialize` has purpose-built parsing for Codex, Windsurf and Kimi, plus a
generic fallback, and nobody on the project has run a real Gemini session
through it since the upstream fix shipped. If `daimon status` shows no fresh
checkpoint, or a serialize error, a parsing gap is the likely cause, and a
report naming your gemini-cli version is welcome.

On gemini-cli **before v0.21.0**, expect capture to show as skipped rather
than written. Briefing injection on `SessionStart` works independently of
capture either way.
