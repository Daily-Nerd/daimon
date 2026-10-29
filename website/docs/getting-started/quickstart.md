---
sidebar_position: 1
description: "Install daimon and get your first agent briefing in six steps, from uv tool install to a checkpoint your next session actually reads."
---

# Quickstart

From install to your first briefing. Six steps, two optional.

## 1. Install the CLI

```sh
uv tool install 'daimon-briefing[pretty]'
```

`pipx install 'daimon-briefing[pretty]'` works identically. The `[pretty]`
extra adds rich tables and panels to `status` and `brief`; without it you get
plain text.

## 2. Connect an LLM

Serialization — turning a finished session into a checkpoint — needs an LLM
endpoint. Run:

```sh
daimon configure
```

If the `claude` CLI is on your PATH, this prints `✓ ready` and you are done —
zero configuration. Otherwise, point daimon at any OpenAI-compatible endpoint:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <YOUR-KEY> --model gemini-2.5-flash
```

Then verify the backend end-to-end before trusting it:

```sh
daimon configure --test
```

This sends one tiny prompt through the resolved backend and reports pass or
fail. Config is written to `~/.daimon/env` — see
[Configuration](./configuration.md) for every variable, and the
[backends matrix](../reference/backends-tested.md) for field-measured model
combinations.

## 3. Hook up your host

Hooks are what capture your sessions. For Claude Code, install the plugin —
it registers the session hooks itself:

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

For Windsurf, Codex, or Gemini CLI, follow your host's page in
[Hosts](../hosts/index.md) — each host has a different hook surface, and the
per-host guides cover the exact registration steps and caveats.

## 4. Fill the briefing from past sessions

Optional, and worth it on a fresh install. Until you end a session there is
nothing to brief from, and the transcripts already on this machine can fill
that gap. `daimon import`
serializes a bounded number of them through the same pipeline the hooks use,
and says what the run will cost before it spends a model call. Run it from the
project directory, with the backend from step 2 in place:

```sh
daimon import --project . --plan
```

`--plan` prints the plan and stops there. Nothing is spent:

```
import plan for app (claude-code): 4 sessions, about 8 model calls
  1  2026-10-07  88 msgs  3 calls  8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e  note: compact-summary-only-context
  2  2026-10-09  30 msgs  2 calls  b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715
  3  2026-10-12  57 msgs  2 calls  5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2
  4  2026-10-14  23 msgs  1 call  c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4
  skipped 0e7b6a52-3c1d-4e8f-9a04-b5c7d2e1f690: modified in the last 10 minutes, may be live
  claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)
  codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)
```

Discovery covers Claude Code and Codex. daimon lists the transcripts under both
hosts' roots and keeps the ones whose recorded working directory is this
project. The 5 newest by session end form the window; `--sessions N` changes
the count. Every skip the plan names comes out of that window and nothing older
takes its place, so the same command rebuilds the same plan. Above, the newest
file was written minutes ago, so it is skipped as possibly live and four
sessions are planned. A session that already has a checkpoint, one shorter
than `DAIMON_MIN_MESSAGES`, a missing file, an unreadable one: each is a named
skip, before any spend. When the whole window is already captured and older
transcripts exist, the plan says `all N newest sessions already captured;
raise --sessions to reach older history`. A run without `--plan` whose window
holds only skips prints `nothing to import` and exits 0. Windsurf and Kimi
record no project in their transcripts, so they are never discovered: pass
`--transcript PATH` with `--project <dir>` typed on the command line. Gemini
CLI and Hermes have no transcript root daimon registers, so import reaches
neither, by discovery or by path.

The call count is a plan: one call per chunk of the rendered transcript plus
the merges. The run stops before the session whose planned calls would take
the spent count past `--max-calls`. The default is the planned total, so a
plain run spends no more than the number it printed, except for the retries
and the fallback rescue of one session, which the plan cannot see.

Now run it:

```sh
daimon import --project .
```

The same plan prints, then the sessions go oldest first, two lines each, and a
summary closes the run:

```
wrote checkpoint: /Users/you/.daimon/checkpoints/8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e.json (took 231s) [import]
[1/4] 8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e: 96,004 tokens
wrote checkpoint: /Users/you/.daimon/checkpoints/b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715.json (took 80s) [import]
[2/4] b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715: 30,118 tokens, 2 quotes downgraded
wrote checkpoint: /Users/you/.daimon/checkpoints/5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2.json (took 101s) [import]
[3/4] 5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2: tokens unreported
wrote checkpoint: /Users/you/.daimon/checkpoints/c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4.json (took 44s) [import]
[4/4] c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4: 18,902 tokens
imported 4 of 4, 8 calls (8 planned), 145,024 tokens reported, 1 session unreported
```

The tokens are what the backend reported for that session. `tokens unreported`
means it reported nothing, and the summary counts those sessions instead of
guessing. `quotes downgraded` counts the quotes the verification could not find
in the transcript. The `note:` in the plan is a separate fact: that session
opens with a compaction summary, so the model never saw what came before it.
Without the `[pretty]` extra, when stdout is not a terminal, or with `NO_COLOR`
or `DAIMON_PLAIN` set, an `importing <session id>...` line prints before each
pair.

The first failed serialize of any kind stops the run. The serializer's own
`error: ...` line prints first, then:

```
[2/4] b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715: error after 3s (the error line is above)
stopped after the first failed session. 1 of 4 imported, 3 left.
run the same command again to resume; done sessions are skipped for free.
imported 1 of 4, 3 calls (3 planned), 96,004 tokens reported
```

Resuming is the same command. The plan is rebuilt over the same window and the
session that landed is now a skip, at no cost. The automatic retry at session
start (`daimon heal`) never touches an import:

```
import plan for app (claude-code): 3 sessions, about 5 model calls
  1  2026-10-09  30 msgs  2 calls  b71f0c3d-2e85-47a6-8c19-d4f6a0b2e715
  2  2026-10-12  57 msgs  2 calls  5a9e3d27-18f4-4c6b-a2d0-7e3b9c1f48a2
  3  2026-10-14  23 msgs  1 call  c4d81f6a-9b3e-4a27-8f05-1e6d2b7c93f4
  skipped 0e7b6a52-3c1d-4e8f-9a04-b5c7d2e1f690: modified in the last 10 minutes, may be live
  skipped 8d2e4b71-c6a9-4f30-b5d2-91e7a3c40f6e: already has a checkpoint
  claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)
  codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions
  not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)
```

The first briefing after an import opens with one note under the greeting, so
nobody mistakes old work for where they just stopped:

```
⚠ Imported history: 4 past sessions, the newest ended 12d ago (2026-10-14), before daimon captured anything live here. Check it before acting on it.
```

The active topic line reads `Active topic (imported, 12d ago): ...` for the
same reason. The note counts the import files still on disk and dates the
newest. When a live capture of the project already exists, it says instead that
the import is `newer than daimon's last live capture here and serialized after
the fact`; a briefing shown through the global fallback says `in that project`
rather than `here`. Both the note and the qualifier end on their own the first
time a live capture becomes the latest checkpoint. There is nothing to clear.

Every refusal and exit code is in the
[`daimon import` row](../reference/cli#set-up) of the CLI reference, and
each [host page](../hosts/) says how far import reaches on that host.

## 5. Teach your agent the protocol (optional, recommended)

Hooks capture sessions; the skill teaches the agent on the other side how to
*use* the briefing — read it at session start, treat `verbatim` items as
immutable quotes, verify stale-looking claims before repeating them:

```sh
daimon skill install claude
```

`daimon skill list` shows the install targets for other hosts. Run `daimon skill install` with no host to let daimon detect what this machine runs, or `daimon skill install --all` to wire every host it finds without being asked.


## 6. End a session, start the next

That's the whole loop:

1. Work a normal session in your agent.
2. End it. The session-end hook serializes a checkpoint in the background.
3. Start the next session. The briefing is injected at session start:

```
While you were away — here's where we left off.

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"

Active topic: Migrating the scheduler off cron to the new worker pool
```

`[✓ verbatim]` means a deterministic checker found that quote in the session
transcript; `[~ inferred]` marks the model's own conclusions. The full
tag system is the point of the product — see [Trust classes](../concepts/trust-classes.md).

You can also read it in a terminal anytime with `daimon brief`.

:::note[Short sessions are skipped by design]
A session shorter than `DAIMON_MIN_MESSAGES` (default: 10 messages) is not
serialized — there is nothing worth remembering in a two-message exchange.
If you are evaluating daimon and want your short test sessions captured,
lower the threshold for now:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env
```
:::

## Check that it's working

```sh
daimon status
```

Status reports capture health honestly — failures, skips, and crashes
included. The lines that matter on a fresh install:

```
project checkpoint: <session id>, written <n>m ago
last serialize result: success — wrote checkpoint: ...
```

If the last serialize failed, the next session start retries it once in the
background: it launches `daimon heal`, which re-serializes the newest failed
session whose transcript is still on disk. One automatic retry per session.
Run `daimon heal` to retry now, `daimon heal --all` for every healable
session on this machine (`--project <dir>` for one project), or `daimon heal
--force` to spend another attempt on a session that already used its retry
(`--session <id>` to pick one).

## Before you end a session

If you are leaving work unfinished, pass the baton — one imperative line the
next session sees above everything else in its briefing:

```sh
daimon handoff "Finish the migration first. Beware: the staging DB is stale."
```

A checkpoint reconstructs what happened; the baton carries what you intended
next. Full story in [the item lifecycle](../concepts/lifecycle.md).

## Where to next

- [CLI reference](../reference/cli.md) — every verb, one page.
- [Configuration](./configuration.md) — every environment variable, including
  the `DAIMON_DISABLE` kill switch.
- [Hosts](../hosts/index.md) — per-host setup detail and known limitations.
- [Team memory](../team/team.md) — share checkpoints with teammates through a
  private git remote (opt-in).
