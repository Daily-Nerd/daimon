# Host setup guides

Daimon closes a capture -> inject loop around whatever agent host you use: a
session-end hook writes a checkpoint, a session-start (or per-prompt) hook
turns the latest checkpoint into a briefing. Support depth varies by what
hook events each host actually exposes.

| Host | Install | Capture | Briefing injection | Status |
|---|---|---|---|---|
| [Claude Code](./claude-code.md) | Plugin (`/plugin install daimon@daimon`) or manual `hook/daimon-hooks.py install` | `SessionEnd` hook spawns `daimon serialize` detached | `SessionStart` hook injects the briefing; `UserPromptSubmit` hook adds proactive recall | live-validated daily |
| [Codex](./codex.md) | Manual `hook/codex-hooks.py install` (from a clone) | Throttled `Stop` hook (Codex has no session-end event) | `SessionStart` hook injects via `additionalContext` | shipped, awaiting first live run |
| [Gemini CLI](./gemini.md) | Manual `hook/gemini-hooks.py install` (from a clone) | Blocked upstream (`gemini-cli#14715` — `transcript_path` is an empty stub) | `SessionStart` hook injects via `additionalContext` | blocked upstream (`gemini-cli#14715`) |
| [Windsurf (Cascade)](./windsurf.md) | `daimon hooks install windsurf` | Throttled serialize on `pre_user_prompt` / `post_cascade_response(_with_transcript)` | None — Cascade has no session-start-equivalent event; the skill instructs the agent to run `daimon brief --team` in the terminal at session start | live-validated |

## Three moving parts

Every host setup combines up to three pieces, installed independently:

- **Hooks** capture your sessions and (where the host supports it) inject the
  briefing as context. Claude Code gets a packaged plugin; other hosts install
  standalone hook scripts via `daimon hooks install <host>` (currently
  Windsurf) or the manual lifecycle scripts under `hook/` (Codex, Gemini, and
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
