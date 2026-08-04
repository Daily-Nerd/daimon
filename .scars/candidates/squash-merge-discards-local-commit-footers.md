---
id: 0
type: landmine
title: Squash-merge replaces the local commit body with the PR body, so conventional-commits footers must live in the PR
severity: medium
confidence: 0.95
created: 2026-08-04
authors: ["claude-code"]
anchors:
  - path: release-please-config.json
  - path: .github/workflows/pr-validation.yml
  - path: .github/workflows/release.yml
evidence:
  - note: "PR #551 was authored specifically to add a BREAKING CHANGE footer. Its local commit carried the footer; the squashed commit on main (0d3d1a8) carries the PR body instead, with no footer. The Release workflow ran green and left the release PR at 0.24.1."
  - note: ".github/workflows/pr-validation.yml:70 states the rule for the subject line: 'squash-merge makes the PR title the commit subject on main'. The same substitution applies to the body, which is the half that is easy to miss."
expires:
  condition: "the repository stops squash-merging, or release-please starts reading PR bodies/labels for breaking-change markers instead of commit footers"
  review_after: 2027-02-04
status: candidate
---

This repository squash-merges. GitHub composes the squashed commit message from
the PR TITLE and PR BODY, discarding whatever the local commits said. The
subject half of this is already documented at
`.github/workflows/pr-validation.yml:70`. The body half is not, and it is the
one that silently breaks releases.

Consequence: any conventional-commits FOOTER that release-please must parse has
to be written into the PR body, not into the local commit message. A
`BREAKING CHANGE:` footer authored locally never reaches `main` and never
reaches release-please, which then computes an ordinary patch bump for a
change that breaks users.

This is not hypothetical. #549 changed `_resolve_command()` so a `claude` on
PATH is no longer adopted implicitly, which stops capture on installs that
relied on it. It was queued to ship as `0.24.1`. #551 was authored to correct
exactly that by adding the missing footer, and #551's own footer was eaten the
same way. The Release workflow reported success both times, because nothing in
the pipeline compares "this PR describes a breaking change" against "this
commit is marked as one".

What to do instead, in order of reliability:

1. Put `!` in the PR TITLE (`fix(scope)!: ...`). The title becomes the commit
   subject verbatim, and `pr-validation.yml`'s regex already permits `!`. This
   is the only marker that cannot be lost by the merge.
2. Additionally place the `BREAKING CHANGE:` footer as the LAST paragraph of
   the PR body, so the rendered changelog entry carries the remedy.

Do not rely on prose. A `## Breaking change` heading in a PR body is invisible
to release-please; only `!` in the subject and a `BREAKING CHANGE:` footer are
parsed. Related: `bump-minor-pre-major` must be true in
`release-please-config.json` for a 0.x breaking change to bump the minor rather
than jumping to 1.0.0.
