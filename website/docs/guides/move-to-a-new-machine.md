---
sidebar_position: 9
description: "Move daimon to a new machine, a new username or a renamed folder without losing a project's history: what to copy, what to leave behind, and the commands in order."
---

# Move daimon to a new machine or folder

A project's memory lives in a bucket: a directory under `checkpoints/` whose name never changes. The bucket records the folder its project lives in, in a file called `root`. Moving a project means telling daimon the new folder. Nothing is renamed and no signed file is rewritten, so the history comes with you.

This page covers three moves:

- a new machine, or a new username on the same one (the long path, below);
- one project folder renamed or moved (jump to [One folder moved](#one-folder-moved));
- a machine where you already ran daimon (read [The new machine must start empty](#the-new-machine-must-start-empty) first).

If a project's briefing says no memory is bound to its folder, or `daimon bucket doctor` reports a bucket whose folder is gone, this is the page it points to.

## Before you copy

Every directory daimon uses can be moved by a variable, so the commands below read the variable and fall back to the default. If you keep settings in `~/.daimon/env`, look there too: `rg DAIMON_ ~/.daimon/env`.

On the old machine, with no session running and your editor or agent closed:

```sh
OLD_CKPT="${DAIMON_CHECKPOINT_DIR:-$HOME/.daimon/checkpoints}"
OLD_TEAM="${DAIMON_TEAM_DIR:-$HOME/.daimon/team}"
OLD_KEYS="${DAIMON_KEYS_DIR:-$HOME/.daimon/keys}"
OLD_ENV="${DAIMON_ENV_FILE:-$HOME/.daimon/env}"
```

A copy taken while a hook is writing can tear a checkpoint from its receipt, which is why nothing may be running.

## On the old machine

1. Retry any session that failed to save. `heal` reads logs that are not copied, so it has to run here:

   ```sh
   daimon heal --dry-run
   daimon heal --all
   ```

2. Record where each project lives, so the new machine can tell which bucket is which. This only writes small identity files and never touches a signed checkpoint. Point `--scan` at the folders that hold your repositories. The first command prints a plan, the second writes it:

   ```sh
   daimon bucket stamp --scan ~/code
   daimon bucket stamp --scan ~/code --apply
   ```

3. If you use a team repository and have local checkpoints not yet pushed, sync them first, so the copy of the team clones is current:

   ```sh
   daimon team sync
   ```

4. Copy. `rsync -a` keeps modification times, and retention reads them. Set the `DEST_` variables to where the files go on the new machine (`you@new-machine:.daimon/checkpoints`, or a folder on an external drive), and create `~/.daimon/team` and `~/.daimon/keys` there first, because `rsync` does not create parent folders.

   ```sh
   DEST_CKPT=you@new-machine:.daimon/checkpoints
   DEST_TEAM=you@new-machine:.daimon/team
   DEST_KEYS=you@new-machine:.daimon/keys
   DEST_ENV=you@new-machine:.daimon/env
   ```

   The checkpoints, without the caches and the half-written files:

   ```sh
   rsync -a \
     --exclude '.chunk-cache/' --exclude '.partials/' --exclude '.birth/' \
     --exclude '*.tmp' --exclude '.pointer.lock' \
     "$OLD_CKPT/" "$DEST_CKPT/"
   ```

   Your own team mirror (it can outlive the flat files):

   ```sh
   rsync -a --exclude '*.tmp' --exclude '.pointer.lock' "$OLD_TEAM/local/" "$DEST_TEAM/local/"
   ```

   The environment file, once. Edit it on the new machine afterwards, because it holds per-machine settings and secrets:

   ```sh
   rsync -a "$OLD_ENV" "$DEST_ENV"
   ```

   The signing keys, only when the old machine is being retired. They are one identity, so copy both files or neither:

   ```sh
   rsync -a "$OLD_KEYS/" "$DEST_KEYS/"
   ```

## What to copy and what to leave

| Copy | Why |
| --- | --- |
| `checkpoints/` (minus `.chunk-cache`, `.partials`, `.birth`, `*.tmp`, `.pointer.lock`) | The source of truth: every bucket, every signed `<session>.json` with its `.receipt`. Keep each pair together. |
| `team/local/` | Your own mirror. `team/<clone>/` only after `daimon team sync`, and it can be fetched again. |
| `keys/` as a pair, or not at all | `signing.seed` and `signing.pub.json` are one identity. Copy both and every receipt verifies, with one signing identity living on two machines. Copy neither and the new machine signs with a key of its own: new receipts verify, and the old ones, signed by the old key, do not verify here. Never copy only the public file: every new receipt would then fail to verify. This follows from reading the code; it was not run end to end. |
| `env`, once | Per-machine settings; edit it after copying. |

| Leave | Why |
| --- | --- |
| `recall.db`, `recall/` | Derived. Rebuilt on first use. |
| `recall_seen/`, `checks/`, `hooks/` | Cooldowns, generated checks and installed copies. Reinstall and regenerate (`daimon hooks install`, `daimon check sync`). |
| `codex/`, `kimi/`, `windsurf/`, `probe/` | Per-host session state of the old machine. |
| `logs/` | `serialize.log` says which sessions were captured. Copying it makes `heal` look for transcripts that are not here. |

## The new machine must start empty

Install daimon and stop there: do not open a session in any project yet. The copy goes into an empty `checkpoints/`. A store that already holds sessions is not supported, because daimon has no way to merge two whole stores. If the new machine has history worth keeping, move that store aside and point `DAIMON_CHECKPOINT_DIR` at it when you want to read it.

Clone or copy your projects to their new folders. Then run everything from here on the new machine.

## On the new machine

1. Rebind the buckets. Old home to new home, for every project that exists in its new place. The first command is a dry run and lists what it would change; read it, then apply:

   ```sh
   daimon bucket remap --from /Users/alice --to /Users/bob
   daimon bucket remap --from /Users/alice --to /Users/bob --apply
   ```

   A bucket whose new folder does not exist yet (a project you have not cloned) is skipped and listed, which is fine: run the same command again after you clone it. A bucket whose new folder already has a bucket of its own, because a session ran there before this step, is listed with the exact `daimon bucket merge` command that folds the two, the new bucket into the old one. Check the names, run it, then run `remap` again:

   ```sh
   daimon bucket merge --from=BUCKET --into=BUCKET --force
   daimon bucket merge --from=BUCKET --into=BUCKET --force --apply
   ```

   The command carries `--force` because the folder exists, and `merge` otherwise refuses to remove a bucket whose folder is still there (a session in that folder would create it again). That is safe here, because `remap` binds the folder to the old bucket right after. Do not open a session in that project between the two commands.

2. A project whose folder is not just the old one under a new home (a rename, a different layout) is bound one at a time. `doctor` finds the bucket by the repository's origin and first commit and prints the command:

   ```sh
   daimon bucket doctor --path .
   daimon bucket adopt --bucket=BUCKET --path . --apply
   ```

3. Look the store over. Repair a ledger only if `daimon status` reports it degraded; the dry run changes nothing:

   ```sh
   daimon ledger repair events --dry-run --project .
   daimon bucket doctor
   ```

4. Regenerate what you did not copy:

   ```sh
   daimon check sync --project .
   ```

5. Check the result from inside a moved project:

   ```sh
   daimon status --project .
   daimon brief --project .
   ```

   The briefing is where you left off on the old machine.

6. Your team author name decides which checkpoints are yours. Keep the old one: set `DAIMON_AUTHOR` in `~/.daimon/env` to the old value, or make `git config user.name` the same on both machines.

7. Optional: bring the old transcripts too. Copy `~/.claude/projects/` and import what has no checkpoint yet. The transcripts record the old folder, and `import` accepts them once `remap` or `adopt` has recorded it as a former root of the project:

   ```sh
   daimon import --project . --plan
   ```

## One folder moved

On the same machine, one project's folder was renamed or moved. Nothing needs copying. Find the bucket from inside the new folder, and bind it:

```sh
daimon bucket doctor --path .
daimon bucket adopt --bucket=BUCKET --path . --apply
```

`adopt` refuses when another bucket already records the new folder (a fresh one born before you ran it). Fold that one with the `bucket merge` command `doctor` prints, then adopt.

## A username change on the same machine

Same as a new machine, without the copy: run the `remap` commands from step 1 with the old and new home.

## Windows

The commands are the same. `%USERPROFILE%\.daimon` is where daimon keeps its files by default, and `remap` takes drive paths:

```sh
daimon bucket remap --from C:\Users\a --to C:\Users\b
```

Paths compare without regard to case or to `/` against `\`, and a move between systems works (`C:\Users\a` to `/Users/b`). Pick your own copy tool and keep modification times. This was checked with Windows path rules in tests, not on a Windows machine.

## If something looks wrong

- The briefing says no memory is bound: the folder was not covered by the `remap` you ran. Run `daimon bucket doctor --path .`.
- `remap` listed a bucket as `collision`: run the `bucket merge` it printed, then `remap` again.
- `remap` skipped a bucket as `live-source`: its old folder still exists on this machine, so it may be a clone that keeps its own history. `--force` moves it anyway.
- `daimon bucket doctor` reports an `unstamped` bucket: it records no folder. `adopt` it onto the right folder.
