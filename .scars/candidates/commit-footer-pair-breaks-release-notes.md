---
id: 0
type: landmine
title: A commit carrying both Closes and Refs footers is dropped from the release notes
severity: medium
confidence: 0.8
created: 2026-08-30
authors: ["claude-code"]
anchors:
  - path: .github/workflows/release.yml
  - pattern: "Refs #"
evidence:
  - commit: 6c8074eab10257253926f27a5ef4748aaed22b0b
  - pr: 861
  - note: "Release run 33333174033 logged: commit could not be parsed: 6c8074e ... then commits: 15 / Considering: 15 commits, from 16."
expires:
  condition: "release-please parses a two-footer commit, or the repo stops using Refs footers"
  review_after: 2027-02-28
status: candidate
---

PR #858 merged as `fix(request): ...` with a body opening `Closes #857` then
`Refs #842`. release-please could not parse that commit, dropped it silently,
and generated 0.37.0's notes from 15 of 16 commits. A shipped user-facing fix
had no release note and nothing failed: the Release workflow reported success,
and the omission is visible only in the run log line "commit could not be
parsed".

Every other commit in v0.30.0..main carries at most one footer, and every one
of those parsed. The two-footer pair is the only structural difference between
6c8074e (dropped) and be3f9ff (kept), whose bodies are otherwise identical in
shape.

What to do instead: put ONE issue footer in a commit body. When a PR closes one
issue and relates to another, keep `Closes #N` in the commit and put the `Refs`
in the PR body, which is not parsed by the generator.

Recovery, if it already happened: regenerating the release PR does NOT help,
because the cause is the commit, not the PR. Add the entry to CHANGELOG.md on
the release branch by hand in the generator's format, and check the published
GitHub Release body separately, since it is generated from the same unparsed
commit set.

The general lesson is the dangerous half: a generator that skips input it
cannot read, while exiting zero, produces a record that is wrong in the
direction of looking complete.
