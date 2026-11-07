# Contributing

The Python package lives in `plugin/`; [uv](https://docs.astral.sh/uv/) drives
everything — no pip, no manual venvs.

## Setup

```bash
git clone https://github.com/Daily-Nerd/daimon.git
cd daimon
uvx pre-commit install
```

`uvx pre-commit install` is a one-time step: it wires the commit-time hooks
(`.pre-commit-config.yaml`), which run **ruff** (check only, no autofix) over
the same paths CI lints, plus the **hook-mirror drift check**
(`scripts/sync_hooks.py --check`). Some hook files are deliberate
byte-for-byte copies of a canonical source; if the drift check goes red, edit
the canonical file and run `uv run python scripts/sync_hooks.py` to propagate.

## Tests

```bash
cd plugin
uv run --extra dev --extra pretty pytest
```

`--extra pretty` matters: the rich-path tests import `rich` unconditionally,
so a bare `uv run pytest` produces false failures.

## Lint

```bash
cd plugin
uv run --extra dev ruff check daimon_briefing tests ../scripts
```

ruff is version-pinned in the dev extra and the pre-commit hook pins the same
version, so the commit-time verdict always matches CI.

## CI gates

Every PR runs pytest on Python 3.10 and 3.13, `lint (ruff + mypy)` (ruff
blocking, mypy advisory for now), scar lint, and the PR convention check.
The `lint (ruff + mypy)` job is intended to be a required status check in
branch protection — flipping that switch is a repo-settings action for the
maintainer, not something the tree controls.

## Commits

PRs merge with a merge commit, so every commit on your branch reaches `main`
as itself and release-please reads each one: the subject becomes a changelog
line, and a `!` in the subject or a `BREAKING CHANGE:` footer in the body is
what marks a breaking change. The PR convention check runs release-please's
own parser over every branch commit, so write each subject as
`type(scope): description`. The PR title and body are not a changelog
source of their own.

The PR title is the one text that must NOT look like that. With the merge
commit message set to the PR title, GitHub's merge button writes the title
under a blank line in the merge commit body, and release-please parses a
`type(scope):` line there as one more commit, so the PR would be listed twice.
The PR convention check therefore fails a title that starts with a word, an
optional scope, an optional `!` and a colon, in any case. Write a plain title,
for example `Request overdue state for open requests (#1281)`; the `type:*`
label on the PR carries the type. Any way of merging is then safe, the merge
button included. To verify a merge commit that already landed, run
`uv run python scripts/merge_pr.py <number> --check`.
