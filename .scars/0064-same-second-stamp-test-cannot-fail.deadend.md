---
id: 64
type: deadend
title: A same-second test of ts/created_at preservation passes even when preservation is broken
severity: medium
confidence: 0.9
created: 2026-08-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/tests/test_amendments.py
  - path: plugin/daimon_briefing/amendments.py
evidence:
  - commit: 657d832
expires:
  condition: "ledger _stamp stops formatting ts at second resolution (%Y-%m-%dT%H:%M:%SZ)"
  review_after: 2027-08-30
status: active
---

Tried and abandoned while clearing amendments off the mypy ratchet (#842).
To pin that reopening a rejected proposal keeps its ORIGINAL `created_at`,
I captured the stamp before `reject()` and asserted it unchanged after the
repropose. It passed. It also passed with the preservation deliberately
broken to `row.get("ts")`, so it asserted nothing.

Cause: `_stamp` formats `ts` with `%Y-%m-%dT%H:%M:%SZ`, second resolution.
Two ledger writes in one test land in the same second, so a preserved stamp
and a freshly written one are the SAME STRING. The assertion cannot fail.
Every ledger module here shares this shape (`amendments`, `refutations`,
`relations`, `requests`), so it is not amendments-specific.

Instead: monkeypatch `<module>.time.time_ns` with an iterator of distinct
seconds (one per `_stamp` call on the path), then assert against literal
stamps. See `test_reopened_proposal_keeps_its_original_created_at`. Always
run the fix backwards, breaking the behavior on purpose, to confirm the
test actually fails before trusting it.
