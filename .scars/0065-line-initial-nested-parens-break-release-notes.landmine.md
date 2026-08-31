---
id: 65
type: landmine
title: A body line starting with a call that has nested parens is dropped from the release notes
severity: medium
confidence: 0.95
created: 2026-08-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: .github/workflows/release.yml
  - path: .github/workflows/pr-validation.yml
evidence:
  - commit: 6c8074eab10257253926f27a5ef4748aaed22b0b
  - pr: 861
  - note: "Release run 33333174033: commit could not be parsed: 6c8074e, then 15 of 16 commits considered. Reproduced with @conventional-commits/parser: original FAILS at 13:42, same message with the Refs footer removed STILL FAILS at 12:42, same message with that one line indented two spaces PARSES."
expires:
  condition: "pr-validation.yml gates on the release-please parser, or release-please stops using @conventional-commits/parser"
  review_after: 2027-02-28
status: active
---

`squash_merge_commit_message` is PR_BODY, so the PR body IS the commit message
release-please parses. A body line whose FIRST token is immediately followed by
`(` containing another `(` fails to parse: the grammar reads it as a
`type(scope)` header and the inner paren is unexpected. The commit is then
dropped from the changelog AND from the version bump, silently, while the
Release workflow exits zero.

Fences and inline backticks do NOT protect. Leading whitespace does, and so
does any text before the token. `foo(bar(x)) and more` fails; `    foo(bar(x))`
and `the foo(bar(x)) call` both parse. A single paren never fails.

THIS SCAR PREVIOUSLY BLAMED A DOUBLE FOOTER (`Closes #N` plus `Refs #N`). That
was correlation, not cause: two-footer commits are rare, so the one dropped
commit happened to be one. Removing the footer does not fix 6c8074e; indenting
one line does. `Refs #N` is the mandated staged-PR convention and is innocent.

What to do instead: indent the line, or put a word before the call. Do not
enumerate forbidden shapes; two prose rules were derived for this and both were
wrong. The gate is running the real parser over title+body in pr-validation.yml.

Recovery, if it already happened: regenerating the release PR does NOT help,
because the cause is the commit. Hand-edit CHANGELOG.md on the release branch
and check the published Release body separately.

The dangerous half is general: a generator that skips input it cannot read,
while exiting zero, produces a record that is wrong in the direction of looking
complete.
