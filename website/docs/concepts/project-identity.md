---
sidebar_position: 8
description: "How daimon knows which project a folder is: a permanent bucket name, the folder the bucket records, explicit commands to change it, and what worktrees and the unbound notice do."
---

# Project identity

daimon keeps a project's memory in a **bucket**, a directory under `checkpoints/`. Which bucket a folder belongs to has one answer, and it never depends on guessing.

## The bucket name is permanent

A bucket is named once, when it is born, from the folder's path (`/Users/me/code/app` becomes `-Users-me-code-app`). After that the name is only an id. Moving the project, renaming the folder, changing your username or copying the store to another machine does not rename it, and no signed checkpoint is ever rewritten. `daimon slug <path>` still prints the name a path would get at birth, which is why it works for a path daimon has never seen.

## The recorded root is the binding

Each bucket has a small file called `root` that holds the folder it belongs to. That file is the only link between a bucket and a place on disk. To find the bucket for a folder, daimon looks for the bucket whose recorded root is that folder, compared without regard to case or to `/` against `\` where the system ignores them. A bucket that records a folder is never handed to a different one.

A bucket from before roots were recorded has no `root`, and keeps answering the way it always did until its first write records one. `daimon bucket stamp` records roots ahead of a move.

## Changing the binding is an explicit act

daimon never rebinds a bucket on its own. A folder that matches nothing gets a new bucket, so a wrong guess can never merge two projects or hand one project's memory to another. The commands that change a binding are yours to run. The ones that write print a plan first and write only with `--apply`, and all of them refuse on a tenant-scoped home:

| Command | Use it when |
| --- | --- |
| `daimon bucket remap --from DIR --to DIR` | A folder or username changed, and every project under it moved the same way. |
| `daimon bucket adopt --bucket NAME --path DIR` | One project moved somewhere `remap` does not cover. |
| `daimon bucket merge --from NAME --into NAME` | One project already has two buckets, for example one born in the new folder before you rebound the old one. |
| `daimon bucket stamp` | Before a move, so each bucket records where its project lives. |
| `daimon bucket doctor` | You want to see which buckets are unbound, shared, empty or left behind. It writes nothing. |

The old root stays in the bucket's identity record as history. `daimon import` uses that history: a transcript written on the old machine records the old folder, and it is accepted for the project once `remap` or `adopt` has recorded that folder as a former root. A transcript from any other folder is still refused. The step-by-step version of a machine move is [Move daimon to a new machine or folder](../guides/move-to-a-new-machine.md).

## Worktrees use their main repository

A session in a linked git worktree uses the bucket of its main repository, so the history, rulings and requests of one project stay in one place. The main repository's own worktree list is the authority, and a `.git` file that points anywhere else binds nothing. A worktree that already has a bucket of its own keeps it until you fold it in with `daimon bucket merge`.

## Two notices, one line each

When a folder has no bucket, the briefing carries one fixed line saying no memory is bound to it and asking the person to run `daimon bucket doctor --path .`. When a worktree still has a bucket of its own beside its main repository's, it carries a second fixed line asking for `daimon bucket doctor`. Neither line contains a bucket name or any project text, because an agent reads the briefing and the survey that names other projects belongs on a person's terminal. Nothing is refused and nothing is changed by either notice.
