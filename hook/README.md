# Agent hook adapters

## Codex

Codex support lives in [CODEX.md](./CODEX.md). It installs a `SessionStart`
hook for briefing injection and a throttled `Stop` hook for transcript capture.

## Gemini CLI

Gemini support mirrors the Claude Code shape, split across two scripts:

- `daimon-gemini-session-start.py` — `SessionStart` hook. Shells out to
  `daimon brief` and injects the result via Gemini's
  `{"hookSpecificOutput": {"additionalContext": ...}}` envelope. Gemini
  requires **pure-JSON stdout** ("Silence is Mandatory") — unlike the Claude
  Code hook, nothing is ever printed raw; operator-facing diagnostics ride
  `{"systemMessage": ...}` instead. SessionStart is advisory-only: exit 0
  always, startup is never blocked.
- `daimon-gemini-session-end.py` — `SessionEnd` hook. Mirrors the Claude Code
  SessionEnd hook (spawns `daimon serialize <transcript_path>` detached), but
  Gemini CLI currently sends `transcript_path` as an **empty stub**
  (`gemini-cli#14715`, upstream limitation as of 2026-07-01), so this hook's
  primary behavior today is a graceful, logged skip. The spawn path is ready
  for when upstream populates the field.
- `gemini-hooks.py` — lifecycle manager (same shape as `codex-hooks.py`):

```sh
python3 hook/gemini-hooks.py install   [--dry-run]
python3 hook/gemini-hooks.py uninstall [--dry-run]
python3 hook/gemini-hooks.py status
```

Install copies both scripts (plus `_daimon_hook_lib.py`) to `~/.gemini/hooks/`
and registers them in `~/.gemini/settings.json` (user layer). Requires the
`daimon` CLI on `PATH` (`uv tool install ./plugin` or equivalent).

## Windsurf (Cascade)

One script, `daimon-windsurf-hooks.py`, is registered for three Cascade hook
events (`pre_user_prompt`, `post_cascade_response`,
`post_cascade_response_with_transcript` — see
[docs.windsurf.com](https://docs.windsurf.com/windsurf/cascade/hooks)):

- **Native transcript preferred:** when `post_cascade_response_with_transcript`
  is registered and Cascade's native `.jsonl` transcript
  (`~/.windsurf/transcripts/<trajectory_id>.jsonl`) exists for the trajectory,
  it is serialized directly — no accumulation needed (#71).
- **Accumulation fallback (#35):** `pre_user_prompt` / `post_cascade_response`
  carry no transcript path, so the adapter appends each turn to its own
  `~/.daimon/windsurf/transcripts/<trajectory_id>.md` in the same
  `**role**:`-marked shape `daimon serialize` already parses.
- **Throttled serialize:** both serialize-capable events fire every turn;
  `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` (default 300s, `0` = every turn)
  gates the spawn per trajectory, sharing one marker so registering both
  events never double-spawns.
- **Self-probing (#62):** any payload shape the adapter can't handle is
  dumped to `~/.daimon/windsurf/unparsed-<event>-<stamp>.json` (at most one
  dump per event name), so the next adapter iteration has real evidence to
  work from instead of another manual probe round.
- **No briefing injection:** Cascade's hook set has no session-start-equivalent
  event, so unlike Claude Code/Codex/Gemini the briefing is not injected as
  context — it's read via the terminal (`daimon brief`). This is a permanent
  host constraint, not a bug (0.8.1 #82).
- Fail-open everywhere; kill switch `DAIMON_DISABLE=1`.

`daimon-windsurf-probe.py` is a separate, standalone dev tool (not installed
by end users) used to capture raw Cascade hook payloads and scan Windsurf's
sqlite state (`--scan-vscdb`) — the ground-truth-gathering step that the
adapter above was built from (#37, #38).

Install via the packaged CLI installer rather than a manual lifecycle script:

```sh
daimon hooks install windsurf   # copies daimon-windsurf-hooks.py, _daimon_hook_lib.py,
                                 # and redact.py to ~/.daimon/hooks/, then prints the
                                 # registration snippet for Cascade's hooks config
daimon hooks list                # hosts with packaged hook scripts
```

`daimon hooks install <host>` (currently: `windsurf` — `daimon hooks list`
shows every host with a packaged installer; Claude Code is intentionally
excluded because the plugin marketplace owns that path, and Codex/Gemini
still use the manual lifecycle scripts above) ships `redact.py` alongside
the hook script(s) so a standalone host adapter can scrub secrets at its own
write sites (transcript accumulation, checkpoint, event log) without
importing the venv-only `daimon_briefing` package — a test keeps this copy
byte-identical to the canonical `plugin/daimon_briefing/redact.py`. Re-run
`daimon hooks install <host>` after every `daimon` upgrade so the installed
scripts (and the bundled `redact.py`) stay in sync with the installed CLI.

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
