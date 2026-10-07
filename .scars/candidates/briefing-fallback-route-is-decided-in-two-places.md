---
id: 0
type: landmine
title: The own-else-global briefing fallback is decided twice, in store.read_latest_result and in the Claude SessionStart hook script, and the two must agree
severity: medium
confidence: 0.8
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: hook/daimon-session-brief.py
  - pattern: "fallback = latest is not None"
evidence:
  - note: "#1132 PR 7a census: hook/daimon-session-brief.py:55-66 slugs the payload cwd, tests <slug>/latest.json, else falls back to the global latest.json and prints a 'global fallback' label; daimon brief decides the same thing through store.read_latest_result (fell_back) and briefing.prepare."
expires:
  condition: "the hook script stops testing pointer files and reads the route fact from `daimon brief` output or a `daimon` verb"
  review_after: 2027-04-07
status: candidate
---

`hook/daimon-session-brief.py` (and its `_hooks/` mirror) decides "is this a
global fallback" for itself, by checking whether `<checkpoint dir>/<slug of the
payload cwd>/latest.json` exists, before it shells out to `daimon brief`. The
CLI decides the same fact from the store: `store.read_latest_result` returns
`fell_back`, `briefing.prepare` carries it, and header-only fallback (#96) is
keyed on it. The two are different code over the same files.

The script reads no checkpoint content, so it needs no view conversion and
cannot leak a withheld value. But if the store's routing rule changes (a torn
own pointer, an unrouted body, a tenant-scoped home), the script's label
("global fallback, checkpoint may be from another project") can disagree with
what the CLI rendered. Change the rule in both, or make the script ask the CLI.
Do not copy the script's file test into another host.
