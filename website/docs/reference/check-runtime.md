---
sidebar_position: 6
description: "How a ruling above your repositories binds the projects beneath it, how an armed check runs before a shell command, and what each host delivers."
---

# Layer rulings and checks at runtime

## Layer rulings

A ruling proposed and ratified at a plain directory above your repositories binds every project beneath it: run `daimon ruling propose` with the rule text and its usual flags, then `daimon ruling ratify <id> --project ~/work` from an interactive terminal, and it arms there and for everything underneath. A layer is any such directory at or below your home directory that carries no `.git` anywhere at or above it and already holds a daimon bucket.

A child project's briefing renders these inherited rulings first, each one tagged `[from ~/work]`, and they count against the child's own cap of 7: the ceremony at the layer warns which of its descendants an activation would push over that cap. `retire`, `revise` and `ratify` on an inherited id refuse inside the child and point at `--project ~/work` instead, since only the layer's own bucket can act on its ruling. `daimon ruling list --inherited` adds the merged view to a project's own list, and each row it adds carries `inherited_from`, in `--json` too. A check attached to a layer ruling arms in every repository below it the next time `daimon hooks install` or `daimon check sync` runs there.

## Checks at runtime

Ratifying a ruling that carries a check writes two things under `~/.daimon/checks`: a manifest naming every armed check and the project directory it belongs to, and one file per check holding the exact body the ruling stores. Every ledger write that can arm or disarm a check rebuilds them; `daimon check sync` rebuilds them on demand, and so does `daimon hooks install`, which prints what it found. A ruling with a withheld check pattern or body arms nothing, and both verbs print one line with how many rulings were skipped for that reason.

On a host with a pre-action hook installed, a matching shell command runs its armed checks before it executes. The hook reads the manifest, keeps the checks whose project directory contains the action's working directory, and runs the ones whose pattern matches the command string. It always exits 0 and writes either one JSON object or nothing: the deny is a decision the hook makes deliberately, never an exit code that a crash could produce by accident.

## What a check gets on each host

What the author asked for is an intent. What a host delivers is a mode, and it is the weaker of the two.

| intent | Claude Code | Codex | Windsurf |
| --- | --- | --- | --- |
| `enforce` | `enforce` | `enforce` | `unsupported` |
| `warn` | `warn` | `record-only` | `unsupported` |
| `record-only` | `record-only` | `record-only` | `unsupported` |

`enforce` returns the host's structured deny and the command does not run. `warn` allows the command and shows the reason. `record-only` says nothing to the host and leaves the run in the log. `unsupported` is what a host gets when daimon has not measured how it delivers a decision at all: Cascade documents a `pre_run_command` event, but nothing measured says what it does with one, so the whole Windsurf column stays `unsupported` rather than claiming an enforcement nobody has seen delivered.

Codex is the interesting cell. It documents no channel for a warning, so `warn` degrades to `record-only` there: the check still runs and the log still says so, and daimon does not claim to have shown the author something the host never rendered.

When more than one armed check matches a command, the strongest FAILING mode decides. An `enforce` check that passed does not block the command for a `warn` check that did not, and the message names every check that failed at that mode or above. A weaker check's reason stays in the log: `record-only` asked for the log and nothing else, and a neighbour that failed harder does not carry it out on its behalf.

A check runs against a **subject**: the command string, a separator, then the contents of every file argument daimon could resolve, each under a header naming the flag it came from. The subject goes to a temporary file at mode 600 and is removed after the run. Its path arrives in `DAIMON_CHECK_SUBJECT`, alongside `DAIMON_CHECK_COMMAND` and `DAIMON_CHECK_RULING`; the working directory is the action's own, and standard input is `/dev/null`.

What the resolver reads:

| form in the command | what daimon does |
| --- | --- |
| `--body-file <path>`, `--body-file=<path>`, `-F <path>`, `-F<path>` | reads the file |
| `--notes-file <path>`, `--notes-file=<path>` | reads the file |
| `-F key=@<path>`, `--field key=@<path>`, `-Fkey=@<path>` | reads the file |
| `<flag> -` with exactly one heredoc in the SAME command | reads the heredoc text |
| `<flag> -` fed by a pipe | unresolved, cause `stdin-pipe` |
| `<flag> -` whose own command carries no heredoc | unresolved, cause `arg-form-unparsed` |
| a command with more than one heredoc or more than one `<flag> -` | unresolved, cause `arg-form-unparsed` |
| any other `@<path>` or `<flag> -` | unresolved, cause `arg-form-unparsed` |

A value attached to a short flag is the same command as a detached one, so `-Fbody.md` is read exactly as `-F body.md` is.

A heredoc belongs to the command it is attached to, and to no other. daimon splits the command string at `&&`, `||`, `;`, `|` and newlines, and a heredoc in one of those pieces can only be read for an argument in that same piece. So `cat <<EOF > note.txt ... EOF` followed by `gh pr create -F -` is unresolved rather than checked against the text `cat` was given. Inside one command the counts still have to be one and one: which heredoc feeds which argument is not a question the command string answers, and a wrong guess would build the subject from text the action never sends.

A command over 64 KiB once its heredoc bodies are set aside is `arg-form-unparsed`: tokenizing one enormous inline argument costs more than the whole hook budget, and a resolver overtaken by the host's timeout lets the action through with no record at all. A long heredoc body does not count toward that, so a large PR body still resolves.

Relative paths resolve against the working directory. A file over 1 MiB is `file-oversize`, one that is not UTF-8 text is `file-binary`, and a missing or unreadable one is `file-missing` or `file-unreadable`. A path that needs the shell to expand it (`$f`, `$(mktemp)`, a backtick, `~/x`, or a glob like `*.md`) is `shell-expansion`: daimon reads the command before the shell runs, so pass a literal path. One argument daimon cannot read makes the whole subject unresolved, whatever the others say.

Every run ends in exactly one of three outcomes, and they never fold together:

| outcome | what it means |
| --- | --- |
| `clean` | the check ran on the full subject and exited 0 |
| `violation` | the check ran on the full subject and exited 1, its own first stderr line is the reason |
| `unresolved` | daimon could not prove the subject clean: an argument it could not read, a check that crashed or exceeded its budget, a body that no longer hashes to what the ruling was ratified with, or no `sh` on the host |

`unresolved` is never rendered as clean and never counted as a violation.

The body runs with a minimal environment: `PATH`, `HOME`, `LANG`, the `LC_*` variables and `TMPDIR`, plus the three above. It is not a sandbox, a check you ratified runs as you, and could do anything you could. Trimming the environment only keeps a script whose job is reading one file from being handed every token in the session.

The runner carries its own budget, `DAIMON_CHECK_TIMEOUT`, five seconds by default against a host hook timeout of ten. That budget is not optional: on the hosts measured so far, a hook that reaches the host's own timeout does not block and the action proceeds, so a check without a budget of its own turns a hang into a silent allow. Overrunning kills the check and its whole process group and reports `unresolved`.

Each run appends one row to `~/.daimon/logs/checks.jsonl`: ids, outcomes, causes, durations, host and mode. No command text, no paths, no subject. The reason shown to the agent may name a path; the log does not.

The file is capped at 256 KiB. Past that the writer keeps the last 64 KiB and drops the older rows, in place, so a hook holding the file open keeps writing to the same one. The counts every surface below reports are counts over what is left, and each of them names the timestamp that window starts at.

Read that log for liveness, not for compliance. A row proves the check RAN. Only `decision_emitted: deny` under `enforce` closes the gap between a check that ran and a check that was honored.

Three surfaces read it for you. `daimon ruling checks` folds it per ruling and host, `daimon stats` reports one line across every host, and `daimon ruling show` adds a `Fired:` line to a single ruling. All three report a timestamp and counts over the retained window, never a last outcome: an append-only log is ordered by append, so the last row is not the current state. An armed check with no rows reads as `never fired`, which is a different answer from zero violations. A log daimon cannot open is a third answer again: every surface says the log is unreadable rather than claiming nothing ran.

Two conventions worth keeping. Arm a new check with intent `warn` first, so a pattern that matches more than you meant costs a warning rather than a blocked action. And run `daimon ruling check try` before you ratify, that is what it is for.

| variable | default | what it holds |
| --- | --- | --- |
| `DAIMON_CHECKS_DIR` | `~/.daimon/checks` | the manifest and the materialized bodies |
| `DAIMON_CHECK_TIMEOUT` | `5` | the runner's budget in seconds, floored at 0.5 |
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | holds `checks.jsonl` |
