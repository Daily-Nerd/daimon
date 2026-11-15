---
sidebar_position: 7
description: "When no briefing appears or a capture failed: read status, retry with heal, what each skipped session means, a ledger that cannot be read, and team sync failures."
---

# Troubleshooting

Start with `daimon status`. It reports capture health honestly, failures and skips included: the project's newest checkpoint and how old it is, the last serialize result, and one line per problem with what to do about it.

## No briefing, nothing captured

- A session shorter than `DAIMON_MIN_MESSAGES` (10 messages by default) is not serialized on purpose. Lower it with `echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env` if you are testing with short sessions.
- Check that the hooks are installed with `daimon hooks status`, and the skills with `daimon skill status`.
- Check that the backend answers with `daimon configure --test`.

## A capture failed

The next session start retries the newest failed session once, in the background, with `daimon heal`. You can run it yourself:

```sh
daimon heal --dry-run     # explain what would be repaired, and why not
daimon heal               # the most recent failure
daimon heal --all         # every healable session on this machine
daimon heal --force       # spend another attempt on a session that used its retry
```

`--project <dir>` limits it to one project and `--session <id>` to one session. A session heal skips is named with its reason:

| reason | what it means |
| --- | --- |
| `retry-exhausted` | the retry already ran and the session still fails; re-run with `--force` |
| `unrecoverable` | no spawn record, or the transcript is gone, so it cannot be healed |
| `hung` | the serialize started and never reported a result, and the transcript is unavailable |
| `admission-refused` | the project's events ledger cannot be read (see below) |
| `repairing` | a repair of that ledger is already running |
| `project-unknown` | a transcript filed under a project directory whose project cannot be recovered; run `daimon serialize <transcript> --project <dir>` |
| `import` | an import attempt, which heal never retries; run `daimon import` again to resume |

## A ledger cannot be read

When a project's `events.jsonl` cannot be read (a conflict marker, a bad byte, an OS error), daimon refuses to write a checkpoint for it, before it calls the model, so the refusal costs nothing. `daimon status` names the ledger and the cure, and the next briefing says how many sessions are waiting. Repair it, then let heal retry them:

```sh
daimon ledger repair events --dry-run
daimon ledger repair events
```

`ledger repair` rejoins rows split across lines and moves every line it cannot parse into `<name>.quarantined-lines` next to the ledger. A refusal is lost only when its transcript is gone.

## A `legacy:` line in status

A bucket written before 0.42.0 exists for this project. `daimon bucket migrate` moves it: see [Upgrade daimon](./upgrade.md#upgrading-across-0420).

## Team sync

Sync is fail-open by design: any degraded outcome leaves you on the last synced
state and never breaks the session.

| Situation | What happens |
|---|---|
| Offline | Sync defers; you keep the last synced team state. Your own new checkpoints commit locally and queue for the next push. |
| Missing git credentials | Git runs non-interactively (`GIT_TERMINAL_PROMPT=0`), it fails fast instead of hanging on a credential prompt, and sync degrades to offline. |
| Concurrent pushes | A rejected push is benign (a teammate won the race). Daimon integrates their change and retries, bounded to 3 attempts, then warns if still rejected. |
| Rewritten shared history | Loud warning; daimon leaves your local copy untouched and refuses to auto-repair. Daimon never force-pushes, resolve it manually with git and your team. |
| Git not installed | Sync is skipped and returns success (rc 0), team memory is simply inactive. |
