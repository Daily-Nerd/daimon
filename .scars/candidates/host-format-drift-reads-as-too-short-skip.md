---
id: 0
type: landmine
title: Host transcript format drift parses to 0 messages and the too-short gate eats it silently
severity: high
confidence: 0.9
created: 2026-08-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/transcript.py
evidence:
  - commit: 64c5fb6
  - note: "issue #622 — Codex CLI 0.146.0→0.147.0 replaced event_msg/user_message and agent_message with item_completed (PascalCase item.type, content-block lists); a 13MB real session parsed to 0 messages and serialize skipped it 9 times as 'transcript too short (0 < 10 messages)' with nothing recorded for heal"
expires:
  condition: "serializer treats a multi-record transcript that parses to 0 messages as a parse failure (loud, heal-visible) instead of a too-short skip"
  review_after: 2026-11-07
---

Host JSONL schemas are explicitly unstable (Codex says so in its docs), and
the parser's per-host branches match exact record shapes. When a host CLI
upgrade renames its event types, the branch collects zero messages, the
generic fallback cannot read payload-nested rows, and the length gate then
classifies the session as "too short" — a legitimate-looking skip, not a
failure. Nothing reaches serialize-crash logging or `daimon heal`; the loss
is invisible until a user notices a missing checkpoint (issue #622, fixed in
64c5fb6). When touching parser branches or the too-short gate: a transcript
with many raw records that parses to 0 messages is a parse failure, never a
short session — and any new host-format support needs a fixture copied from a
field transcript, not from that host's docs.
