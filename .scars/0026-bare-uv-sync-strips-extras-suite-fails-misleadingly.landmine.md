---
id: 26
type: landmine
title: Bare `uv sync` in plugin/ strips dev+pretty extras — suite then fails looking like code regressions
severity: medium
confidence: 0.9
created: 2026-07-29
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/pyproject.toml
evidence:
  - note: 2026-07-29 session: after `uv sync -q`, pytest binary vanished; after `uv sync --extra dev`, 31 test_render failures (rich missing) read as regressions on an untouched module; `uv sync --extra dev --extra pretty` restored 2037 passed
  - pr: 424
  - pr: 425
  - note: 2026-07-30: recurred TWICE independently — the #418 and #419 fix agents each hit the identical 31-failure test_render shape in fresh git worktrees (`uv sync --extra dev` alone); both recovered only via `uv sync --all-extras`. Two later agents avoided it solely because their prompts pre-warned them. Three independent hits in two days.
expires:
  condition: "dev/pretty move from [project.optional-dependencies] to default dependency-groups, or a sync wrapper/Makefile target becomes the documented path"
  review_after: 2026-10-29
status: active
---

Ran `uv sync` in plugin/ to refresh the env after a version bump. It removes
everything not in default dependencies: pytest disappears, and rich/rich-argparse
go with it. The trap is the failure shape, not the failure: with only `--extra
dev` restored, 31 test_render/test_version failures look exactly like code
regressions in modules nobody touched, inviting a debugging spiral on healthy
code. Both extras are required for a green suite: `uv sync --extra dev --extra
pretty`. If the suite suddenly fails wide on render/version tests right after
an env operation, fix the env first, read the diffs second.
