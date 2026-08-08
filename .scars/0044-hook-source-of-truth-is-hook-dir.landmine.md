---
id: 44
type: landmine
title: Editing plugin/daimon_briefing/_hooks/ is silently reverted — hook/ is the source of truth
severity: medium
confidence: 0.9
created: 2026-08-06
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/_hooks/
  - path: scripts/sync_hooks.py
evidence:
  - note: 2026-08-06, #607: edited the package copy of daimon-windsurf-hooks.py, ran scripts/sync_hooks.py, and the edit was overwritten — two tests kept failing against code that had reverted itself
expires:
  condition: "sync_hooks.py syncs bidirectionally, or the package copy becomes a symlink/generated artifact that cannot be edited by hand"
  review_after: 2027-02-01
status: active
---

`scripts/sync_hooks.py` copies ONE direction: `hook/<name>.py` INTO
`plugin/daimon_briefing/_hooks/<name>.py`. Its output line reads
`synced plugin/... <- hook/...`, and the arrow is the whole contract.

Editing the package copy therefore looks like it worked — the file on disk
holds your change, tests may even pass — until anything runs the sync, at
which point the change is gone with no error. The pre-commit "hook mirror
drift" check does NOT save you: it only asserts the two copies match, so a
reverted edit passes it happily.

Edit `hook/<name>.py`, then run `uv run python scripts/sync_hooks.py`.
Grep the arrow direction in the script's output before trusting any hook
edit, and be aware that hook scripts cannot import the package (they run
standalone with a `sys.path` insert), so shared values like
`DAIMON_WINDSURF_DIR` must be read from `os.environ` on both sides rather
than imported from `config`.
