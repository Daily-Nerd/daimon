---
description: "Run daimon on a runtime with no adapter: the neutral transcript contract, the host-run serialize command, the environment a host sets, what the ledger shows, and which runtimes have native adapters."
---

# Any other runtime

A runtime without a daimon adapter can still feed it. Convert the session to
the neutral transcript below, then run `daimon serialize` yourself after the
runtime exits. The checkpoint is the same one a hook would write.

This is the path for a host that runs the agent inside its own sandbox, where
a SessionEnd hook cannot reach the daimon home. The host owns the run, so
`--host` makes the CLI do what a hook would have done around the spawn.

## The neutral transcript

A `.jsonl` file, one JSON object per line:

| Field | Required | Meaning |
|---|---|---|
| `role` | yes | `user` or `assistant` |
| `content` | yes | the message text, a string |
| `uuid` | no | a stable id for the message, used to point back at it |
| `said_by` | no | who said a user message, for a session with more than one person |

```jsonl
{"role": "user", "content": "The nightly export fails on the staging box. Can you find out why?", "uuid": "m-001"}
{"role": "assistant", "content": "I read the export log. It dies on a permission error writing to /srv/exports.", "uuid": "m-002"}
{"role": "user", "content": "Is that the cron user or the deploy user?", "uuid": "m-003", "said_by": "Dana"}
{"role": "assistant", "content": "The cron user. The deploy user owns the directory and the cron user has no write bit.", "uuid": "m-004"}
{"role": "user", "content": "Then give the cron user the group, not the whole directory.", "uuid": "m-005", "said_by": "Dana"}
{"role": "assistant", "content": "Decision: add the cron user to the exports group and leave the directory mode alone.", "uuid": "m-006"}
```

Other row shapes daimon already reads (Claude Code, Codex, Windsurf and Kimi
transcripts, and plain markdown with `user:` and `assistant:` markers) are
detected by content. You do not pass a format, and there is no `--format`
flag. If you are writing a converter, emit the shape above.

## Run it

```sh
daimon serialize --host my-runner \
  --project /work/my-repo \
  --session 7f3c9a10 \
  /path/to/session.jsonl
```

- `--host` is the name your runtime gives itself: lowercase letters, digits
  and `-`, 1 to 40 characters, starting with a letter or digit. Anything else
  exits 2.
- `--project` is the project directory the checkpoint belongs to. Without it,
  daimon uses `DAIMON_PROJECT_DIR`, then the working directory.
- `--session` is the session id. Without it the file name (minus `.jsonl`) is
  the id. Pass it when your transcript files are not named for the session.

What `--host` adds before the run starts:

- It writes the spawn line to the ledger (below), so a failed run still knows
  its project and can be healed.
- It refuses to start when a live serialize for the same session is already
  running (a heartbeat younger than `DAIMON_HUNG_AFTER`, 1800 seconds by
  default). The exit code is 75, with one line on stderr, and nothing is
  written to the store.
- It sets `DAIMON_CAPTURE_HOST` to your name for provenance, unless the
  environment already carries one.
- If an exception escapes the command, it appends one entry to
  `serialize-crash.log` in the log directory before the process dies. The file
  is trimmed to a fixed cap, the same one the hooks use.

Without `--host` the command behaves as it always did.

## Environment a host sets

Set these in the environment of the `daimon serialize` process. Each is read
from the process environment first and `~/.daimon/env` second.

| Variable | Use |
|---|---|
| `DAIMON_PROJECT_DIR` | the project directory, the same value as `--project`. Set both on a tenant scoped home, where a `--project` that differs from the process's own project is refused |
| `DAIMON_TENANT_SCOPED` | set to scope the home to one tenant: a read or write that would leave the process's own project is refused |
| `DAIMON_SESSION_SPEAKER` | one label for who spoke in the session. It applies to the whole process, so it is right only when one session is one person; use `said_by` per message otherwise |
| `DAIMON_AUTHOR` | the author name stamped on what the run captures. Without it daimon uses git, then the OS user |

The serialize also needs an LLM backend, as it does everywhere. See the
[quickstart](../getting-started/quickstart) for the key and model settings.

## What the ledger shows

A host-run writes its lines to `serialize.log` in the log directory, the file
`daimon status` and `daimon heal` read. First the spawn line, then the result
line the command prints:

```text
2026-11-08T14:02:11Z my-runner: spawned serialize for 7f3c9a10 (reason: host-run, project: /work/my-repo) (transcript: /path/to/session.jsonl)
wrote checkpoint: /home/you/.daimon/checkpoints/7f3c9a10.json (took 41s)
```

A refused duplicate leaves a skip line instead and no spawn line:

```text
2026-11-08T14:02:15Z my-runner: skipped serialize for 7f3c9a10 (already in flight)
```

`daimon status` lists a run that has a spawn line and no result as running,
then as hung once it passes `DAIMON_HUNG_AFTER`. A failed run is retried once
by `daimon heal`, routed to the project on the spawn line, like a Claude Code
session.

## Adapters today

| Runtime | How it reaches daimon |
|---|---|
| Claude Code | hooks, see [Claude Code](./claude-code.md) |
| Codex | hooks, see [Codex](./codex.md) |
| Windsurf | hooks, see [Windsurf](./windsurf.md) |
| Kimi Code | hooks, see [Kimi Code](./kimi.md) |
| Hermes | in process, no file and no command |
| Gemini CLI | none by design: its hook sends no usable transcript, so daimon has no parser for it. See [Gemini CLI](./gemini.md) |
| OpenCode | none. Convert to the neutral transcript and use the command above |

Any other runtime takes the path on this page.
