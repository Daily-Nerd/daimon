# Claude Code

Claude Code is the live-validated host: daily dogfood, full loop (serialize
-> carry -> brief -> recall), field incidents recorded in
[research/LOGBOOK.md](https://github.com/Daily-Nerd/daimon/blob/main/research/README.md).

## Install (plugin — recommended)

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

The plugin registers the `SessionStart` / `UserPromptSubmit` / `SessionEnd`
hooks itself via `.claude-plugin/plugin.json` + `hooks/hooks.json`. Order
doesn't matter relative to installing the `daimon` CLI: if the hooks land
before the CLI, sessions start normally and the hook prints a one-line install
hint instead of a briefing.

> **Don't mix install paths.** Plugin users must **not** also run the manual
> hook installer below — both paths coexisting registers the hooks twice
> (double briefings, double serialize LLM calls). Switching from manual to
> plugin: run `python3 hook/daimon-hooks.py uninstall` first.

## Install (manual / dogfood hooks)

For a repo clone without the plugin system, `hook/daimon-hooks.py` is a
lifecycle manager (same shape as SCAR's `scar-hooks.py`):

```sh
python3 hook/daimon-hooks.py install   [--dry-run]
python3 hook/daimon-hooks.py uninstall [--dry-run]
python3 hook/daimon-hooks.py status
```

Install copies the hook scripts to `~/.claude/hooks/` and registers them
under `SessionStart` / `SessionEnd` in `~/.claude/settings.json` (idempotent;
settings backed up before every mutation). Requires the plugin CLI on PATH:
`uv tool install ./plugin` (or equivalent) so `daimon` resolves (the hooks
also accept the deprecated `daimon-briefing` alias as a fallback) — reinstall
after updating `plugin/` so the CLI picks up `.jsonl` transcript support,
which `SessionEnd` depends on.

## What each script does

Three scripts close the capture -> inject loop, whichever install path
registers them:

- **`daimon-session-brief.py`** — `SessionStart` hook. Reads the payload from
  stdin and shells out to the installed `daimon brief` CLI (single source of
  truth for rendering); prints the briefing to stdout, which Claude Code
  injects as session context. **Per-project routing:** the payload `cwd` is
  slugged (Claude Code style: `/Users/x/proj` -> `-Users-x-proj`) and this
  project's `<checkpoint-dir>/<slug>/latest.json` is preferred; if the
  project has no checkpoint of its own, the global `latest.json` is used and
  the briefing header is labeled `(global fallback — checkpoint may be from
  another project)`. The cwd is forwarded to the CLI via `DAIMON_PROJECT_DIR`
  so both route identically. No `cwd` in the payload -> global behavior,
  unlabeled. Fail-open: always exits 0, prints a one-line diagnostic on
  failure instead of dying silently. Respects `DAIMON_DISABLE` and
  `DAIMON_CHECKPOINT_DIR`.
- **`daimon-session-end.py`** — `SessionEnd` hook. Reads the payload from
  stdin and spawns `daimon serialize <transcript_path>` as a detached
  background process — serialization is an LLM call (30s+ on long sessions)
  and must never block `/exit`. The payload `cwd` is passed to the child as
  `DAIMON_PROJECT_DIR`, so the serializer writes this project's
  `<slug>/latest.json` in addition to the global `latest.json` (kept for
  backward compatibility and the fallback path). No `cwd` -> child env
  untouched, global-only as before. Diagnostics and serializer output land in
  `~/.daimon/logs/serialize.log` — both the `wrote checkpoint: <path> (took
  Ns)` success line and named-error lines (`... after Ns`) carry elapsed
  seconds. Fail-open, respects `DAIMON_DISABLE`. Not fired on hard kills
  (terminal closed, SIGKILL) — briefings can still be stale, which is why the
  `SessionStart` header shows checkpoint age.

  LLM credentials come from `~/.daimon/env` (see the
  [plugin README](https://github.com/Daily-Nerd/daimon/blob/main/plugin/README.md) Connect an LLM section) — hooks
  inherit the host process environment, not your shell profile, so
  `DAIMON_LLM_API_KEY` / `DAIMON_LLM_MODEL` / `DAIMON_LLM_BASE_URL` belong in
  that file (chmod 600). Without it, serialize fails fast with a named error
  in `~/.daimon/logs/serialize.log`.
- **`daimon-prompt-recall.py`** — `UserPromptSubmit` hook (proactive "you
  worked on this before" recall). Fires on every prompt, pipes the prompt to
  `daimon recall-inject` on stdin, and injects a one-line pointer when the
  prompt overlaps a prior open loop. Because it fires per-prompt, failures
  are silent (exit 0, no output) — the only thing it ever prints is a real
  suggestion — and slash commands (host directives, not work statements)
  never match.

These two capture/inject scripts are wired in one of two mutually exclusive
ways — pick ONE (both at once double-fires every session: two briefing
injections, two serialize LLM calls). See the install sections above.

## Teach the agent the protocol

```sh
daimon skill install claude      # ~/.claude/skills/daimon/SKILL.md
```

`daimon skill show` prints the skill content; `daimon skill list` shows which
scopes each host supports. Re-run install after upgrading `daimon` to refresh
the content.

## Verify

```sh
daimon status
```

`daimon status` reports capture health honestly, including failures, skips,
and crashes; a failed capture self-heals on the next start. End a session ->
a checkpoint is written; start the next -> the briefing appears.
