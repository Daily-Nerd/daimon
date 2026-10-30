---
id: 0
type: landmine
title: Host hook output limits are external and change silently; the brief byte default must come from a measurement, not one spill message
severity: high
confidence: 0.8
created: 2026-10-30
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/config.py
  - pattern: "DAIMON_BRIEF_MAX_BYTES"
evidence:
  - note: "2026-10-30, #1269: Claude Code 2.1.314 trims hook stdout and spills it when its length passes 10000 UTF-16 units, replacing it with a 2 KB preview (largest unspilled briefing 8,920 chars, smallest spilled 10,508). Codex 0.157.0 delivers about 10,000 chars with a truncation banner. Kimi Code 0.43.1 showed no cut up to 25,728 chars. The old default 11264 came from one spill message that read 12.8KB and sat above both limits."
expires:
  condition: "the per-host limits in docs/configuration.md are re-measured against current host versions and the default still sits under the tightest of them, or hosts stop capturing hook stdout"
  review_after: 2027-04-30
status: candidate
---

The byte budget for `daimon brief` exists because a host hook captures the
whole stdout and cuts it at a limit the host owns. Those limits are not ours,
are not documented by the hosts, and change between releases. The first
default (11264) was inferred from a single spill message and was never
checked against a second host or a second measurement, so both Claude Code
and Codex were already cutting briefings the budget called safe.

Two traps follow. First, the limit counts what the host captures, which is
everything `daimon brief --label` prints: the label, the fallback and
in-flight warnings and the cross-project mark all count, not just the body.
They are collected as `lead` lines and charged to the same budget in
`render.render_brief`. A new line printed around the body without going
through that budget reopens the gap. Second, the unit differs by host
(UTF-16 units, characters, possibly tokens); bytes are never fewer than any
of them, which is why the budget is in bytes.

Before changing the default, re-measure each host and update the table in
`docs/configuration.md` (and its Spanish mirror). Keep the default under the
tightest measured limit with margin.
