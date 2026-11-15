---
sidebar_position: 1
description: "Fill a project's briefing from the transcripts already on this machine: the plan, the cost, resuming a stopped run and what the first briefing says."
---

# Import past sessions

Until you end a session there is nothing to brief from. `daimon import` fills that gap from the transcripts already on this machine, through the same pipeline the hooks use, and says what the run will cost before it spends a model call. Run it from the project directory, with a backend configured (`daimon configure --test` checks one).

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
[command notes for `daimon import`](../reference/cli/notes.md), and
each [host page](../hosts/) says how far import reaches on that host.
