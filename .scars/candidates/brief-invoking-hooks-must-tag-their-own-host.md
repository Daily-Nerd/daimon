---
id: 0
type: landmine
title: A host's daimon-brief-invoking hook must pass project_env(cwd, "<host>") itself — the sibling session-end/stop hook doing it is not enough
severity: medium
confidence: 0.85
created: 2026-09-23
authors: ["claude-code"]
anchors:
  - path: hook/daimon-session-brief.py
  - path: hook/daimon-codex-session-start.py
  - path: hook/daimon-kimi-user-prompt-submit.py
  - path: plugin/daimon_briefing/_hooks/daimon-kimi-user-prompt-submit.py
  - pattern: "project_env\(cwd\)"
evidence:
  - note: "#1089 discovery, 2026-09-23"
  - note: "2026-09-24: hook/daimon-kimi-user-prompt-submit.py (source of truth per scar #44) still calls lib.project_env(cwd) with no host argument at lines 104, 125 and 143, as of this commit — a live instance of the gap this scar describes, not yet filed as a fix."
expires:
  condition: "daimon brief grows a --host flag or another host-identity channel that does not depend on each hook script opting in"
  review_after: 2027-03-23
status: candidate
---

`config.capture_host()` reads `DAIMON_CAPTURE_HOST`, and `project_env(cwd, host)`
already knows how to set it — but before #1089 the accessor was wired for
exactly one door per host: the session-end/stop hook that calls
`project_env(cwd, "claude-code")` / `"codex"` / `"kimi"` before spawning capture.
Every hook that instead shells out to `daimon brief` (`daimon-session-brief.py`,
`daimon-codex-session-start.py`, `daimon-kimi-user-prompt-submit.py`) called
`lib.project_env(cwd)` with NO host argument, so `daimon brief` always saw
`config.capture_host() is None` — capture attribution worked while rendering
stayed permanently host-blind, and nothing failed loudly because "unknown host"
degrades safely (prose everywhere) rather than raising.

A future host adapter (or a refactor of an existing one) that copies the
session-end script's `project_env(cwd, "<host>")` call is not proof the
brief-invoking script for the SAME host got the identical treatment — they are
different files, and grepping one and not the other misses it. Anything that
wants to know which host a `daimon brief` render is happening for (#1089's
compact-ruling gate is the first consumer) must verify BOTH call sites for a
host, not just the one that already worked for capture. Windsurf's
`daimon-windsurf-hooks.py` never calls `daimon brief` at all (no auto-briefing,
by design), so it has no analogous gap to close.
