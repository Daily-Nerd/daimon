---
id: 23
type: landmine
title: Manual CLI checks under /tmp silently fail slug match — macOS /tmp is a symlink to /private/tmp
severity: low
confidence: 0.9
created: 2026-07-15
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "project_slug"
evidence:
  - note: 2026-07-15: two agents independently lost time to this while manually exercising `daimon resolve` (#303/#304 verification). Both saw 'no checkpoint for this project yet — nothing to resolve' and assumed a code fault; the checkpoint existed, under a different slug.
expires:
  condition: "project_slug resolves symlinks (or the CLI reports a slug mismatch instead of 'no checkpoint')"
  review_after: 2027-01-15
status: active
---

On macOS `/tmp` is a symlink to `/private/tmp`. `store.project_slug()` derives the bucket from the
project path, so a checkpoint written while cwd resolves one way does not match a CLI run that
resolves the other. The scoped read (`store.read_latest(project_dir=..., fallback=False)`) then finds
nothing.

The failure is silent and misleading: `daimon resolve` prints **"no checkpoint for this project yet —
nothing to resolve"**, which reads as "the checkpoint is missing", not "your slug doesn't match". The
same shape will hit any scoped read (`brief`, `resolve`) driven from a `/tmp` path.

Only bites hand-rolled CLI verification (pytest fixtures use `tmp_path`, which is already
`/private/var/...`). Use `/private/tmp/...` explicitly, or compute the slug with
`store.project_slug(dir)` and place the checkpoint under `<ckpt-dir>/<slug>/latest.json`.
