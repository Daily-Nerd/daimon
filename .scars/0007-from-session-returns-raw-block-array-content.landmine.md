---
id: 7
type: landmine
title: transcript.from_session returns RAW hermes messages (block-array content), not flattened strings — consumers must flatten via _text_of or crash
severity: high
confidence: 0.95
created: 2026-06-30
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/transcript.py
  - path: plugin/daimon_briefing/harvest.py
evidence:
  - pr: 81
  - issue: 76
expires:
  condition: "from_session is changed to flatten content to str before returning (then all consumers get plain strings)"
  review_after: 2027-06-30
status: active
---

`transcript.from_session()` returns `db.get_messages_as_conversation()` output UNCHANGED.
On Claude Code, assistant `content` is frequently a BLOCK ARRAY
(`[{"type":"text",...}, {"type":"thinking",...}, {"type":"tool_use",...}]`), NOT a
string — despite the module docstring saying messages normalize to `{role, content:str}`.
The docstring describes the `_text_of`-normalized shape, but `from_session` skips that step.

Any consumer that treats `content` as a string crashes on real sessions. This bit `harvest.detect`
(PR #81): `re.split(content)` raised `TypeError: expected string or bytes-like object, got 'list'`,
the hook swallowed it, and the harvester silently produced ZERO candidates every real session while
every string-only unit test stayed green. `serializer.py` already guards this with an inline
`isinstance(content, list)` flatten — proof the shape is live and the trap is easy to miss.

Before feeding `from_session` output to anything string-shaped, flatten each message's content with
`transcript._text_of(...)` (handles str / list-of-text-blocks / junk → str, dropping thinking+tool_use).
And add a block-array fixture — string-only tests will not catch this.
