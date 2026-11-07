---
id: 0
type: landmine
title: A transcript of host bookkeeping rows only now fails as unreadable-transcript and heal retries it
severity: medium
confidence: 0.6
created: 2026-11-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/serializer.py
  - pattern: "def require_readable"
evidence:
  - note: "issue 1292 made rows with no admitted conversation a read failure (UnreadableTranscript). A Claude Code file holding only rows such as file-history-snapshot or summary has rows and zero conversation, so it takes the same path as a Codex format drift. Not measured against real session files."
expires:
  condition: "the loader classifies known host bookkeeping row types as an excluded code, or a measured corpus shows such files do not occur"
  review_after: 2027-05-07
status: candidate
---

`serializer.require_readable` cannot tell a file the loader failed to read from a
file that honestly holds no conversation, because both have rows and zero
admitted turns. The row account does tell them apart a little: a session that
only ever dropped rows as `no-role` looks like drift, one that only has
`excluded` rows was filtered on purpose, and the gate treats both as failures
(the issue's invariant, scar 0045).

If a host writes transcript files that legitimately carry no conversation,
`daimon status` will count an error and `daimon heal` will retry once, then
report `retry-exhausted`. The fix is not a skip in `require_readable`: name the
host's bookkeeping row types as an excluded code in `transcript.excluded_reason`
(the single predicate, scar 0149) so they leave the failure condition, and add
the new code to `EXCLUDED_ROWS` and the docs table.
