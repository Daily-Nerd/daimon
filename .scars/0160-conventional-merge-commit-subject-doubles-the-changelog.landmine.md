---
id: 160
type: landmine
title: A merge commit whose subject is the PR title makes release-please list every change twice
severity: medium
confidence: 0.95
created: 2026-10-23
authors: ["Kibukx"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: .github/workflows/pr-validation.yml
  - path: release-please-config.json
evidence:
  - note: Archived with user authorization on 2026-11-10. Superseded by pr-title-must-not-parse-as-a-conventional-commit: a conventional PR title can also duplicate changelog entries through the merge commit body.
  - note: v0.59.2 CHANGELOG lists `fix(scope): one owner decides every read scope` twice, as branch commit 6a14b12 and again as merge commit c601e16 (PR
  - pr: 1176
  - note: archived 2026-11-10: Obsolete advice was previously archived by the owner decision. Bulk promotion reactivated it. Superseded by scar 168, which covers conventional PR titles in merge commit bodies.
expires:
  condition: "release-please skips merge commits, or the repo stops merging with a merge commit"
  review_after: 2027-04-23
status: archived
---

PRs here merge with a merge commit, so every branch commit reaches main as
itself and release-please parses each one. The merge commit is parsed too. With
the repo setting `merge_commit_title=PR_TITLE` its subject is the PR title,
which is conventional by the PR title gate, so release-please counts the PR
once per branch commit and once more for the merge: every fix in v0.59.2
appears twice in the changelog (6a14b12 and c601e16 are the same change).

The fix is a repo setting, not code: `merge_commit_title=MERGE_MESSAGE` gives
the merge commit the default `Merge pull request #N from ...` subject, which
release-please drops as unparsable, leaving the branch commits as the only
changelog source. The gates in `pr-validation.yml` read the branch commits for
exactly that reason (#1176) and are correct in both states of the setting.

Do not "fix" the duplicates by loosening the PR title gate so the merge subject
fails to parse: the title still names the PR in the list, and a title that
parses is harmless once the merge subject is the default one. Do not point the
gates back at the PR title and body either; whichever subject the merge commit
carries, the body is not what release-please reads for the branch commits.
