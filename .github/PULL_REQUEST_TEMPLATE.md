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
- conventional title — squash-merge makes it the commit subject on main
- branch named type/description (lowercase), where type is one of:
  feat fix chore docs style refactor perf test build ci revert
  NOT the content area: `research/foo` fails. A PR's head branch cannot be
  retargeted, so a wrong name means closing and reopening the PR, not renaming
  it. Pick the name from the KIND of change before the first push.
- the body must PARSE as a conventional commit. Squash makes title+body the
  commit release-please reads, and one it cannot parse is dropped from the
  changelog and from the version bump while the release still succeeds. The
  usual cause is a body line whose first token is immediately followed by an
  open paren containing another open paren; indent that line two spaces or put
  a word before it. Fences and backticks do not help (scar 0065).
- breaking change? put the `!` in the TITLE (`feat!:` / `fix(scope)!:`).
  Local commit footers do NOT survive the squash; a body that says
  "breaking change" without a title `!` or a `BREAKING CHANGE:` body
  footer fails validation (#561, scar 0041)
-->

## What

## Why

## Tests

<!-- What you ran and what it proves. New behavior needs a test that failed before the change. -->
