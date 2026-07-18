# Codex

Codex is code-verified and unit-tested (`test_codex_hooks.py`), but has zero
recorded live sessions — the adapter and installer ship, but no logbook entry
documents a real Codex session completing the capture -> inject loop yet.
Treat "runs on Codex" as inferred until one is on record.

## Install

Adds Daimon's capture -> inject loop to Codex from the released package (no repo
clone needed):

```sh
daimon hooks install codex
```

This copies both hook scripts and their shared helper to `~/.codex/hooks/` and
registers `SessionStart` and `Stop` in `~/.codex/hooks.json`, preserving any
unrelated entries already there. It is idempotent — re-run it after every
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
uv tool install ./plugin
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
- **`daimon-codex-stop.py`** — `Stop` hook. Codex exposes `Stop` at turn
  scope, not as a clean session-end event, so this hook serializes
  opportunistically and is throttled by `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL`
  (default `300` seconds per session). Set it to `0` to serialize every turn,
  or set `DAIMON_CODEX_SERIALIZE_ON_STOP=0` to disable Codex capture while
  leaving briefing injection installed.

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
