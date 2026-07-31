---
id: 31
type: landmine
title: HOME-overridden shells silently skip GPG signing when gpgsign lives only in global config
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["claude-code", "Kibukx"]
anchors:
  - pattern: "git commit"
  - path: scripts/
  - path: .github/
violation: "--no-gpg-sign"
evidence:
  - pr: 441
  - note: "Both original commits of PR #441 (a7d08f8, f755588) landed with no gpgsig header at all; branch protection rejected the merge. Re-signed as a29707d/2fd85ac."
expires:
  condition: "signing config no longer depends on repo-local git config (e.g. enforced by a pre-push hook or CI check that rejects unsigned commits)"
  review_after: 2026-10-30
status: active
---

Commits made by sub-agents in sandboxed shells landed UNSIGNED with no error,
and branch protection blocked the merge (PR #441, both commits, 2026-07-30).
Cause: `commit.gpgsign=true` + `user.signingkey` lived only in
`~/.gitconfig`, and the sandbox overrides HOME — git silently loads no global
config and treats signing as simply "off". Git emits no warning for absent
gpgsign; the failure is invisible until the merge is rejected.

Mitigation now in place: signing config is set in the repo's `.git/config`
(worktrees share it), so a broken-GPG environment fails loudly instead of
committing unsigned. That file is not tracked — a fresh clone loses it, so
re-run `git config --local commit.gpgsign true` and set `user.signingkey`
after cloning. After ANY automated commit, verify with
`git log -1 --format=%G?` (must print G, never N) before pushing. Never pass
`--no-gpg-sign` to get past a signing error — report and stop instead
(standing user rule: report-and-fail, never silent-unsigned).
