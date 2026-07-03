# Codex hooks

Adds Daimon's capture -> inject loop to Codex.

- `daimon-codex-session-start.py` — `SessionStart` hook. Reads the latest
  project checkpoint and returns Codex `additionalContext` JSON, so the briefing
  is injected as developer context.
- `daimon-codex-stop.py` — `Stop` hook. Codex exposes `Stop` at turn scope, not
  as a clean session-end event, so this hook serializes opportunistically and is
  throttled by `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL` (default `300` seconds per
  session). Set it to `0` to serialize every turn, or set
  `DAIMON_CODEX_SERIALIZE_ON_STOP=0` to disable Codex capture while leaving
  briefing injection installed.
- `codex-hooks.py` — lifecycle manager:

```sh
python3 hook/codex-hooks.py install   [--dry-run]
python3 hook/codex-hooks.py uninstall [--dry-run]
python3 hook/codex-hooks.py status
```

Install copies both hook scripts to `~/.codex/hooks/` and registers them in
`~/.codex/hooks.json`. After installing, open `/hooks` in Codex to review and
trust the hook definitions. Codex skips untrusted hook definitions until you do.

Requires the `daimon` CLI on `PATH` (the deprecated `daimon-briefing` alias also
works as a fallback):

```sh
uv tool install ./plugin
```

Codex docs note that `transcript_path` is provided for convenience but its
format is not a stable interface. Daimon's JSONL parser is intentionally
best-effort and ignores unknown rows rather than treating raw JSON as transcript
text.
