# Gemini CLI

Gemini support mirrors the Claude Code shape, split across two scripts. The
briefing hook is shipped, but serialize **cannot run end-to-end today**:
capture is staged behind upstream `gemini-cli#14715` (`transcript_path`
stub) — half a loop, by upstream constraint, not a daimon bug.

## What each script does

- **`daimon-gemini-session-start.py`** — `SessionStart` hook. Shells out to
  `daimon brief` and injects the result via Gemini's
  `{"hookSpecificOutput": {"additionalContext": ...}}` envelope. Gemini
  requires **pure-JSON stdout** ("Silence is Mandatory") — unlike the Claude
  Code hook, nothing is ever printed raw; operator-facing diagnostics ride
  `{"systemMessage": ...}` instead. `SessionStart` is advisory-only: exit 0
  always, startup is never blocked.
- **`daimon-gemini-session-end.py`** — `SessionEnd` hook. Mirrors the Claude
  Code `SessionEnd` hook (spawns `daimon serialize <transcript_path>`
  detached), but Gemini CLI currently sends `transcript_path` as an **empty
  stub** (`gemini-cli#14715`, upstream limitation as of 2026-07-01), so this
  hook's primary behavior today is a graceful, logged skip. The spawn path is
  ready for when upstream populates the field.

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

Until `gemini-cli#14715` is resolved upstream, expect `daimon status` to show
capture as skipped rather than written — briefing injection on `SessionStart`
still works independently of capture.
