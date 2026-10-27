---
description: "Which agent hosts daimon supports and how deep each goes: capture and briefing injection on Claude Code, Codex, Gemini CLI and Windsurf."
---

# Host setup guides

Daimon closes a capture -> inject loop around whatever agent host you use: a
session-end hook writes a checkpoint, a session-start (or per-prompt) hook
turns the latest checkpoint into a briefing. Support depth varies by what
hook events each host actually exposes.

| Host | Install | Capture | Briefing injection | Import | Status |
|---|---|---|---|---|---|
| [Claude Code](./claude-code.md) | Plugin (`/plugin install daimon@daimon`) or manual `hook/daimon-hooks.py install` | `SessionEnd` hook spawns `daimon serialize` detached | `SessionStart` hook injects the briefing; `UserPromptSubmit` hook adds proactive recall | automatic: `~/.claude/projects/<slug>/*.jsonl`, matched on the recorded cwd | live-validated daily |
| [Codex](./codex.md) | `daimon hooks install codex` | `SessionEnd` hook serializes on graceful end; throttled `Stop` hook covers everything else | `SessionStart` hook injects via `additionalContext` | automatic: `$CODEX_HOME/sessions` and `archived_sessions`, matched on the `session_meta` cwd | live-validated capture (since 2026-08-06) |
| [Gemini CLI](./gemini.md) | Manual `hook/gemini-hooks.py install` (from a clone) | `SessionEnd` hook spawns `daimon serialize` once `transcript_path` is non-empty (gemini-cli v0.21.0 or later; the `gemini-cli#14715` stub is fixed there) | `SessionStart` hook injects via `additionalContext` | unsupported (no transcript file) | capture unverified: no real Gemini session run through serialize since the upstream fix |
| [Windsurf (Cascade)](./windsurf.md) | `daimon hooks install windsurf` | Throttled serialize on `pre_user_prompt` / `post_cascade_response(_with_transcript)` | None: Cascade has no session-start-equivalent event; the skill instructs the agent to run `daimon brief --team` in the terminal at session start | path-given (records no project, pass `--transcript`) | live-validated |
| [Kimi Code](./kimi.md) | `daimon hooks install kimi` | `SessionEnd` hook serializes on interactive exit; throttled `Stop` hook is the only path in print mode | `UserPromptSubmit` hook injects with your first prompt (no session-start channel) | path-given (records no project, pass `--transcript`) | measured on one live session (2026-09-09) |

The Import column is what `daimon import` does with a host's past sessions,
the bounded first fill in
[Fill the briefing from past sessions](../getting-started/quickstart#4-fill-the-briefing-from-past-sessions).
An `automatic` host is discovered from `--project` alone. A `path-given` host
goes in by `--transcript`, because nothing in its file names the project. An
`unsupported` host has no transcript file daimon can read; Hermes, which has
no page here, sits on that side with Gemini CLI.

## Three moving parts

Every host setup combines up to three pieces, installed independently:

- **Hooks** capture your sessions and (where the host supports it) inject the
  briefing as context. Claude Code gets a packaged plugin; other hosts install
  standalone hook scripts via `daimon hooks install <host>` (Windsurf,
  Codex, Kimi Code) or the manual lifecycle scripts under `hook/` (Gemini, and
  Claude Code without the plugin).
- **The skill** (`daimon skill install <host>`) teaches the agent on the other
  side of the hook how to use what the hooks capture — read the briefing at
  session start (pulling it with `daimon brief --team` when the host injects
  nothing, e.g. Windsurf), treat `verbatim` items as immutable quotes, verify
  stale-looking claims before repeating them.
- **The `daimon` CLI** is the single source of truth every hook shells out to
  for serialization, rendering, and storage — install it once
  (`uv tool install 'daimon-briefing[pretty]'`) and every host's hooks share
  the same checkpoint store.

Pick your host above for the full setup walkthrough.
