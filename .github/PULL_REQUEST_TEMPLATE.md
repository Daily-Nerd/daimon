Closes #

<!--
CI gates on this PR (pr-validation.yml):
- body links an issue labeled status:approved (Closes/Fixes/Resolves #N;
  use "Refs #N" for a stage of a multi-PR issue so merging doesn't close it —
  the final stage uses Closes)
- exactly one type:* label on the PR
- conventional title — squash-merge makes it the commit subject on main
- branch named type/description (lowercase)
- breaking change? put the `!` in the TITLE (`feat!:` / `fix(scope)!:`).
  Local commit footers do NOT survive the squash; a body that says
  "breaking change" without a title `!` or a `BREAKING CHANGE:` body
  footer fails validation (#561, scar 0041)
-->

## What

## Why

## Tests

<!-- What you ran and what it proves. New behavior needs a test that failed before the change. -->
