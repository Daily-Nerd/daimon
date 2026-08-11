---
id: 49
type: landmine
title: redact.py syncs package to hook (opposite of the hook scripts) and must import stdlib only
severity: medium
confidence: 0.85
created: 2026-08-10
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/redact.py
  - path: hook/redact.py
  - pattern: "from \. import|from daimon_briefing"
evidence:
  - commit: a5f3a64
  - pr: 661
  - note: "2026-08-10, #660/#661 — added `from . import normalize` to redact.py; it is copied verbatim next to the standalone Windsurf hook scripts, which run with a sys.path insert and cannot resolve the package"
expires:
  condition: "redact.py stops being shipped standalone beside the hook scripts, or the hook copies become real imports of the installed package"
  review_after: 2027-02-01
status: active
---

`redact.py` exists three times: `plugin/daimon_briefing/redact.py` (canonical),
`plugin/daimon_briefing/_hooks/redact.py`, and `hook/redact.py`. A test pins the
first two byte-identical and `scripts/sync_hooks.py --check` gates the third.

Two traps, and the second contradicts [[0044]].

**It may import stdlib ONLY.** The file lives inside the package, so
`from . import normalize` looks completely ordinary and passes every local test.
It breaks the standalone hooks, which run with a `sys.path` insert and cannot
resolve `daimon_briefing`. Duplicate the few lines you need and comment both
sides as paired, as the NFKC fold does against `normalize.compat_fold`.

**Sync direction is REVERSED here.** Scar 0044 teaches that `hook/` is the
source of truth and package copies get overwritten. For `redact.py` it is the
opposite: `sync_hooks.py` reports `synced hook/redact.py <- plugin/daimon_briefing/redact.py`.
Edit the package copy, then sync. Editing `hook/redact.py` loses the work.
