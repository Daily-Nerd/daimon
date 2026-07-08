# Daimon

Your AI coding agent forgets everything between sessions. **Daimon** writes a small
cognitive checkpoint when a session ends, then renders a "while you were away"
briefing when the next one starts — so the agent resumes from a faithful record of
where you left off instead of a confident guess. Everything runs locally: per-project
JSON checkpoints plus a derived SQLite index, no server, no external memory backend.

Every briefing item carries a trust class. `✓ verbatim` items are pinned to an exact
quote from the transcript and are never reworded. `~ inferred` items are the agent's
own conclusions and are allowed to evolve. Items carried from older sessions are
tagged `[carried]`. Knowing which memories are quotes and which are guesses is the
whole point.

## Install

```sh
pip install daimon-briefing
# or, as an isolated tool:
uv tool install daimon-briefing
```

Add the `[pretty]` extra for rich tables and panels in `status`/`brief`
(plain text works without it):

```sh
uv tool install 'daimon-briefing[pretty]'
```

The installed command is `daimon`. Requires Python 3.10+, stdlib-only at runtime.

## Connect an LLM (one time)

Writing a checkpoint needs an LLM endpoint. If the `claude` CLI is on your PATH you
are zero-config — `daimon configure` prints `✓ ready` and you are done. Otherwise
point it at any OpenAI-compatible endpoint:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <YOUR-KEY> --model gemini-2.5-flash

daimon configure --test    # send one tiny prompt and confirm the backend works
```

Config lives in `~/.daimon/env` (hooks run with the host's inherited environment, not
your shell profile). Kill switch: `DAIMON_DISABLE=1`.

## Hook up your host

**Claude Code (plugin — recommended):**

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

The plugin registers the `SessionStart`/`SessionEnd` hooks itself.

**Windsurf** (and other hosts that ship packaged hooks):

```sh
daimon hooks install windsurf    # copies the hook script + prints the registration snippet
daimon hooks list                # hosts with packaged hook scripts
```

**Codex and other hosts** use the manual hook installer from a repository clone — see
the [GitHub docs](https://github.com/Daily-Nerd/daimon).

Then end a session → a checkpoint is written; start the next → the briefing appears.

## Quickstart without a host

The CLI also works on any plain-text or markdown transcript, no host required:

```sh
daimon serialize path/to/transcript.md   # transcript → checkpoint
daimon brief                             # render the "while you were away" briefing
daimon status                            # did the last checkpoint get written?
```

## Teach your agent the protocol

Hooks capture your sessions; the skill teaches the agent on the other side to read the
briefing at session start and treat `verbatim` items as immutable quotes:

```sh
daimon skill install claude    # also: codex, windsurf, cursor, gemini
daimon skill list              # which scopes each host supports
```

## Commands

| Command | What it does |
|---|---|
| `daimon brief` | Render the "while you were away" briefing from the latest checkpoint |
| `daimon status` | Checkpoint presence/age + last capture outcome; reports failures honestly |
| `daimon recall <terms>` | Full-text search over your whole checkpoint history |
| `daimon resolve <item>` | Mark a checkpoint item resolved so it stops carrying forward |
| `daimon reverify <item>` | Evidence-gated reopen of a resolved item |
| `daimon heal` | Re-serialize the most recent failed session, if it can be done safely |
| `daimon stats` | Local usage + capture aggregates (nothing is transmitted) |
| `daimon configure` | Detect/repair the LLM backend |
| `daimon hooks install <host>` | Install packaged host hook scripts |
| `daimon skill install <host>` | Install the agent skill for a host |

Run `daimon --help` or `daimon <command> --help` for the full surface, including
code anchors (`daimon anchor`) and opt-in team memory (`daimon team`).

## Docs

Full documentation, architecture, and the research trail live in the GitHub
repository: **https://github.com/Daily-Nerd/daimon**

License: Apache-2.0 · Org: [Daily-Nerd](https://github.com/Daily-Nerd)
