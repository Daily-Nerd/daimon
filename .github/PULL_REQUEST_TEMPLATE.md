Closes #

<!--
CI gates on this PR (pr-validation.yml):
- body links an issue labeled status:approved (Closes/Fixes/Resolves #N;
  use "Refs #N" for a stage of a multi-PR issue so merging doesn't close it —
  the final stage uses Closes), except:
  - allowlisted documentation-only PRs labeled type:docs
  - allowlisted CI-only PRs labeled type:ci AND status:approved on the PR
  - scars-only PRs, whose human sign-off happened at promotion
  Mixed changes never receive an issue bypass.
- exactly one type:* label on the PR
- conventional title, type(scope): description. PRs merge with a merge
  commit, so the title names the PR (and the merge commit) but release-please
  reads the branch commits, not the title and not this body.
- branch named type/description (lowercase), where type is one of:
  feat fix chore docs style refactor perf test build ci revert
  NOT the content area: `research/foo` fails. A PR's head branch cannot be
  retargeted, so a wrong name means closing and reopening the PR, not renaming
  it. Pick the name from the KIND of change before the first push.
- every commit on the branch must PARSE as a conventional commit. Each one
  reaches main as itself, so each subject is a changelog line, and a commit
  release-please cannot parse is dropped from the changelog and from the
  version bump while the release still succeeds. The usual cause is a body
  line whose first token is immediately followed by an open paren containing
  another open paren; indent that line two spaces or put a word before it.
  Fences and backticks do not help (scar 0065).
- breaking change? put the `!` in the SUBJECT of the commit that breaks
  (`feat!:` / `fix(scope)!:`) or a `BREAKING CHANGE:` footer in that commit's
  body. A commit that says "breaking change" with neither marker fails
  validation (#561, scar 0041). A `!` in the PR title marks nothing.
-->

## What

## Why

## Tests

<!-- What you ran and what it proves. New behavior needs a test that failed before the change. -->
