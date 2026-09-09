---
description: "Set up daimon on Codex. SessionEnd capture, throttled Stop coverage, rollout transcript parsing, and an honest note on how far validation goes."
---

# Codex

Codex is code-verified, unit-tested (`test_codex_hooks.py`), and live-validated
on the capture side: real Codex sessions have been serialized into checkpoints
since 2026-08-06 — the maintainer's serialize log records both `codex-session-end`
and throttled `codex-stop` captures from rollout transcripts, and checkpoints
whose session id is the rollout file are on record. The transcript parser tracks
Codex's rollout format as it drifts (0.147.0's `item_completed` events are
handled since daimon 0.27.0). Scope honestly stated: validation is one
maintainer machine deep, not a fleet.

## Install

Adds Daimon's capture -> inject loop to Codex from the released package (no repo
clone needed):

```sh
daimon hooks install codex
```

This copies the four hook scripts and the modules they share to
`~/.codex/hooks/` and registers `SessionStart`, `SessionEnd`, `Stop` and
`PreToolUse` in `~/.codex/hooks.json`, preserving any unrelated entries
already there. It is idempotent — re-run it after every
`uv tool upgrade daimon-briefing` to refresh the scripts to match the installed
CLI. After installing, open `/hooks` in Codex to review and trust the hook
definitions — Codex skips untrusted hook definitions until you do.

A stale installed copy keeps *working* on old behavior, so drift is invisible.
Run `daimon hooks status` to audit the installed copies against the packaged
versions (CURRENT/STALE/MISSING, plus the `hooks.json` registration state); it
exits non-zero when anything drifted, and `daimon hooks install codex` refreshes
it in place.

Requires the `daimon` CLI on `PATH` (the deprecated `daimon-briefing` alias
also works as a fallback):

```sh
uv tool install 'daimon-briefing[pretty]'
```

### Manual install (from a clone)

Working from a source checkout, the standalone lifecycle manager offers the same
integration plus `uninstall` and `status`:

```sh
python3 hook/codex-hooks.py install   [--dry-run]
python3 hook/codex-hooks.py uninstall [--dry-run]
python3 hook/codex-hooks.py status
```

## What each script does

- **`daimon-codex-session-start.py`** — `SessionStart` hook. Reads the latest
  project checkpoint and returns Codex `additionalContext` JSON, so the
  briefing is injected as developer context.
- **`daimon-codex-session-end.py`** — `SessionEnd` hook. Serializes the
  finished session in the background when Codex ends it gracefully.
- **`daimon-codex-stop.py`** — `Stop` hook. Codex exposes `Stop` at turn
  scope. `SessionEnd` covers the graceful end of a session, so this hook serializes
  opportunistically and is throttled by `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL`
  (default `300` seconds per session). Set it to `0` to serialize every turn,
  or set `DAIMON_CODEX_SERIALIZE_ON_STOP=0` to disable Codex capture while
  leaving briefing injection installed.

- **`daimon-codex-pre-action.py`** — `PreToolUse` hook, matcher `Bash|shell`.
  **This is the first daimon hook that can fail a host action.** Before a
  shell command runs, it runs this project's armed checks against it and
  returns Codex's structured deny when a check ratified with intent `enforce`
  reports a violation or daimon could not read what the command sends. Codex
  documents no channel for a warning, so intent `warn` degrades to
  `record-only` here: the run is logged and nothing is shown. Exits 0 on every
  path. See [Checks at runtime](../reference/cli#checks-at-runtime).

  Every run appends one row to `~/.daimon/logs/checks.jsonl`. A row proves the
  check RAN. Only `decision_emitted: deny` under `enforce` closes the gap
  between a check that ran and a check that was honored. The file is capped at
  256 KiB, the last 64 KiB kept, so the counts daimon reports from it cover a
  window and every surface that prints them says where that window starts.

  `DAIMON_DISABLE=1` in the host's environment turns every daimon hook off,
  this one included, and is the way out of an `enforce` check whose pattern
  matches more than you meant: the command that retires the ruling is itself
  a shell action the check would otherwise deny.

  The budget is shared across the whole action, not given to each check, so
  on a crowded manifest a later check can find the time already spent and
  report `unresolved`, which denies under `enforce` and warns under `warn`.
  Keep a project's armed checks few and their bodies fast.

Codex docs note that `transcript_path` is provided for convenience but its
format is not a stable interface. Daimon's JSONL parser is intentionally
best-effort and ignores unknown rows rather than treating raw JSON as
transcript text.

## Teach the agent the protocol

```sh
daimon skill install codex       # managed block in ~/.codex/AGENTS.md
```

On the shared `AGENTS.md` file, daimon only ever touches its own marker
block — `daimon skill uninstall codex` removes exactly that block. Re-run
install after upgrading `daimon` to refresh the content.

## Verify

```sh
daimon status
```

`daimon status` reports capture health honestly, including failures, skips,
and crashes.
