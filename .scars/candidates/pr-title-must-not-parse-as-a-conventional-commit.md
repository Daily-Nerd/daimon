---
id: 0
type: fence
title: The PR title gate rejects conventional titles on purpose, because the merge body is the title
severity: medium
confidence: 0.9
created: 2026-11-06
authors: ["Kibukx"]
anchors:
  - path: .github/workflows/pr-validation.yml
  - path: .github/PULL_REQUEST_TEMPLATE.md
evidence:
  - note: "Corrects the candidate conventional-merge-commit-subject-doubles-the-changelog, which says a title that parses is harmless and tells editors not to loosen the title gate. With merge_commit_message=PR_TITLE the title is written into the merge commit body, and release-please splits that body into a second commit, so a parsing title lists the change twice. scripts/merge_pr.py --check reports exactly this for a button merge."
  - pr: 1176
expires:
  condition: "release-please skips merge commits, or the repo stops writing the PR title into the merge commit"
  review_after: 2027-05-06
status: candidate
---

The step `Check PR Title Is Not A Conventional Commit` looks inverted, and it is
meant to be. The merge commit body is the PR title (repo setting
`merge_commit_message=PR_TITLE`). release-please splits a commit message at a
blank line followed by `type(scope):`, so a title shaped that way becomes one
more changelog line next to the one from the branch commit.

An earlier candidate argued that a conventional title is harmless once the merge
subject is the default one. That holds for the subject only; the body still
carries the title. Do not restore the "title must be conventional" rule, and do
not narrow the regex to a list of known types: the parser accepts any word before
the colon, and the match is case-insensitive on purpose.

The type of a PR is carried by its `type:*` label. The gates that matter for the
changelog still read the branch commits, which must parse. Release-please PRs
(skipped by the job) and Dependabot PRs (hidden `ci` type) are outside the rule.
