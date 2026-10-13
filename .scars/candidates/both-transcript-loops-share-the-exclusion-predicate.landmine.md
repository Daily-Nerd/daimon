---
id: 0
type: landmine
title: The message loop and the coverage sidecar must share excluded_reason, or compacted sessions lose coverage
severity: high
confidence: 0.85
created: 2026-10-12
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/transcript.py
  - pattern: "def excluded_reason"
evidence:
  - commit: 1373190
expires:
  condition: "coverage is computed from the same pass that builds the messages"
  review_after: 2027-04-12
status: candidate
---

`_from_jsonl` builds the messages and `_claude_coverage` builds the coverage
rows in a second, separate pass. Coverage is `available` only when the two
counts are equal. An attachment row has top-level type `attachment`, which the
coverage pass already ignores, so a rule for attachments could live in one
loop. A compaction summary is different: it is `type: user`, so the coverage
pass counts it. Dropping it from the message loop alone leaves coverage one
row longer than the messages and flips every compacted session to
`coverage_incomplete`, silently, with no error.

Both loops call `transcript.excluded_reason`. Add a new exclusion there and
nowhere else. `tests/test_transcript_exclusions.py` has a test that patches
the predicate for one caller and shows the flip.
