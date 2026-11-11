---
id: 154
type: landmine
title: A new host bookkeeping row type is dropped, so a file of only those rows fails as unreadable-transcript
severity: medium
confidence: 0.7
created: 2026-11-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/transcript.py
  - pattern: "_BOOKKEEPING_ROWS"
evidence:
  - note: issue 1292. Read-only census of 951 Claude Code transcripts on one machine: 6 held no user or assistant row; 5 of them only session-owner rows, 1 only rows with no type at all. Naming the bookkeeping types as excluded codes turns the five into a no-conversation skip; the sixth (unknown shape) stays a failure by design.
expires:
  condition: "the host's bookkeeping row types are read from a declared host schema instead of a list in transcript.py"
  review_after: 2027-05-07
status: active
---

`serializer.require_readable` fails a transcript that has rows and no admitted
conversation, and skips (`no-conversation`) only when EVERY row was excluded by
a named rule. A row type the loader does not know is dropped (`no-role`), so a
file made of a type nobody named yet is an error, retried once by `heal`, then
`retry-exhausted`. That is intended: it is the scar 0045 signal.

When a host ships a new bookkeeping row type and sessions that only hold it
start failing, add the type to `_BOOKKEEPING_ROWS` in `transcript.py` (the code
is the type) and to the docs table and its Spanish mirror. Do not special-case
it in `require_readable`, and do not exclude by "has no role": that would hide
the format drift the gate exists to catch. A type is excluded only while the row
names no role, so naming a type can never hide a turn. Rows with no `type` at
all (one machine file had them) stay dropped.
