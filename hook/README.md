# Agent hook adapters

## Codex

Codex support lives in [CODEX.md](./CODEX.md). It installs a `SessionStart`
hook for briefing injection and a throttled `Stop` hook for transcript capture.

## Claude Code dogfood hooks

Closes the full capture→inject loop in Claude Code (which does not load hermes
plugins; the hermes path in `plugin/` is unaffected):

- `SessionEnd` writes a checkpoint from the ending session's transcript
- `SessionStart` injects the latest checkpoint as a briefing

- `daimon-session-brief.py` — SessionStart hook. Reads the payload from stdin
  and shells out to the installed `daimon brief` CLI (single source of
  truth for rendering); prints the briefing to stdout, which Claude Code injects
  as session context. **Per-project routing:** the payload `cwd` is slugged
  (Claude Code style: `/Users/x/proj` → `-Users-x-proj`) and this project's
  `<checkpoint-dir>/<slug>/latest.json` is preferred; if the project has no
  checkpoint of its own, the global `latest.json` is used and the briefing
  header is labeled `(global fallback — checkpoint may be from another
  project)`. The cwd is forwarded to the CLI via `DAIMON_PROJECT_DIR` so both
  route identically. No `cwd` in the payload → global behavior, unlabeled.
  Fail-open: always exits 0, prints a one-line diagnostic on failure instead of
  dying silently. Respects `DAIMON_DISABLE` and `DAIMON_CHECKPOINT_DIR`.
- `daimon-session-end.py` — SessionEnd hook. Reads the payload from stdin and
  spawns `daimon serialize <transcript_path>` as a detached background
  process — serialization is an LLM call (30s+ on long sessions) and must never
  block `/exit`. The payload `cwd` is passed to the child as
  `DAIMON_PROJECT_DIR`, so the serializer writes this project's
  `<slug>/latest.json` in addition to the global `latest.json` (kept for
  backward compatibility and the fallback path). No `cwd` → child env untouched,
  global-only as before. Diagnostics and serializer output land in
  `~/.daimon/logs/serialize.log` — both the `wrote checkpoint: <path> (took
  Ns)` success line and named-error lines (`... after Ns`) carry elapsed
  seconds, so checkpoint generation time is visible in production. Fail-open,
  respects `DAIMON_DISABLE`.
  Not fired on hard kills (terminal closed, SIGKILL) — briefings can still be
  stale, which is why the SessionStart header shows checkpoint age.

  Note: this fires for EVERY Claude Code session in every project, and each
  fire costs one LLM serialize call. The briefing slot is per-project: each
  project's last session wins its own `latest.json`; sessions in other projects
  can no longer hijack it. Kill switch: `DAIMON_DISABLE=1`.

  LLM credentials come from `~/.daimon/env` (see `plugin/README.md`
  Configuration) — hooks inherit the host process environment, not your shell
  profile, so `DAIMON_LLM_API_KEY` / `DAIMON_LLM_MODEL` / `DAIMON_LLM_BASE_URL`
  belong in that file (chmod 600). Without it, serialize fails fast with a
  named error in `~/.daimon/logs/serialize.log`.
These two scripts are wired in one of two mutually exclusive ways — pick ONE
(both at once double-fires every session: two briefing injections, two
serialize LLM calls):

- **Plugin (recommended):** the repo is a Claude Code plugin
  (`.claude-plugin/plugin.json` + `hooks/hooks.json` reference these scripts
  via `${CLAUDE_PLUGIN_ROOT}`). Install: `/plugin marketplace add
  Daily-Nerd/daimon` then `/plugin install daimon@daimon`.
- **Manual (Codex / non-plugin hosts):** `daimon-hooks.py` — lifecycle manager
  (same shape as SCAR's `scar-hooks.py`):

```sh
python3 hook/daimon-hooks.py install   [--dry-run]
python3 hook/daimon-hooks.py uninstall [--dry-run]
python3 hook/daimon-hooks.py status
```

Install copies both hooks to `~/.claude/hooks/` and registers them under
`SessionStart` / `SessionEnd` in `~/.claude/settings.json` (idempotent;
settings backed up before every mutation). Requires the plugin CLI on PATH:
`uv tool install ./plugin` (or equivalent) so `daimon` resolves (the hooks also
accept the deprecated `daimon-briefing` alias as a fallback) — reinstall after
updating `plugin/` so the CLI picks up `.jsonl` transcript support, which
SessionEnd depends on.
