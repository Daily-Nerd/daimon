---
id: 0
type: fence
title: checks.sync/audit reused directly on a ruling-layer path is safe only because layer_scopes already excludes every git-shadowed candidate
severity: medium
confidence: 0.8
created: 2026-09-23
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/checks.py
  - path: plugin/daimon_briefing/config.py
evidence:
  - note: "#1095 (issue Daily-Nerd/daimon#1095): sync_layers/audit_layers call checks.sync(layer)/checks.audit(layer) directly on each config.layer_scopes(project_dir) entry, with no special-casing"
expires:
  condition: "config.layer_scopes drops or weakens its #2 guard (excluding any candidate that is, or sits above, a git working tree), or config.resolve_project_dir's git-toplevel walk changes direction"
  review_after: 2027-03-01
status: candidate
---

`checks.sync_layers`/`checks.audit_layers` (#1095) call `checks.sync(layer)`/
`checks.audit(layer)` straight on each path `config.layer_scopes(project_dir)`
returns, with no resolution step of their own. That only works because
`checks.sync`/`checks.audit` internally re-resolve their argument through
`config.resolve_project_dir`, which walks upward via `git rev-parse
--show-toplevel` to find a project's real root — and a layer directory, by
`layer_scopes`'s own eligibility rule #2, is guaranteed to have no `.git`
anywhere at or above it. So the walk finds nothing, returns the path
unchanged, and the layer syncs/audits keyed on exactly the directory
`layer_scopes` named.

`layer_scopes`'s own docstring warns against resolving a layer candidate
through `resolve_project_dir` for a DIFFERENT reason (it would walk from the
CHILD's root, not the layer's, and could collapse a plain ancestor into a
nested repo below it) — that warning is about calling the resolver on the
child's path while iterating ancestors, not about calling it later on the
layer path itself. It is easy to over-read that warning as "never pass a
layer path through resolve_project_dir at all" and reach for a raw-path sync
helper instead. Don't: `checks.sync`/`checks.audit` MUST resolve their input
(every other caller relies on that), and doing so on an already-qualified
layer path is a no-op, not a hazard. If `layer_scopes` ever stops excluding
git-shadowed candidates, this composition silently starts collapsing a
layer's checks into whatever repo sits below it — re-verify this fence before
loosening that guard.
