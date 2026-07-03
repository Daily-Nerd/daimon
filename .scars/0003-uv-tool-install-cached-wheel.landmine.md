---
id: 3
type: landmine
title: uv tool install --force reuses cached wheel when version is unchanged
severity: low
confidence: 0.9
created: 2026-06-10
authors: [claude]
anchors:
  - path: plugin/pyproject.toml
  - pattern: "uv tool install"
evidence:
  - "2026-06-10: after editing plugin source, `uv tool install --force ./plugin` reported success but the installed daimon-briefing still printed the OLD conflated error message; `--force --reinstall` picked up the edits. Version stayed 0.1.0 both times."
expires:
  condition: "plugin adopts per-change version bumps, or install docs/scripts always pass --reinstall"
  review_after: 2027-06-10
status: candidate
---

`uv tool install --force <local-dir>` resolves against the cached wheel for an
unchanged version number — source edits are silently absent from the installed
CLI. Verification then tests stale code while reporting success. Use
`uv tool install --force --reinstall ./plugin` after any source change, or bump
the version.
