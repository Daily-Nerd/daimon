---
id: 0
type: landmine
title: class lost needs an absolute transcript path, an in-process capture logs the bare session id there
severity: medium
confidence: 0.8
created: 2026-11-21
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/ledger.py
  - path: plugin/daimon_briefing/hooks.py
evidence:
  - commit: 8bd2aa3
expires:
  condition: "the in-process failure line records no transcript token when there is no file, or records it in a field of its own"
  review_after: 2027-05-21
status: candidate
---

`ledger._transcript_gone` decides class `lost` (a known transcript path whose
file no longer exists). `hooks._ledger_failure` writes the bare session id in
the `(transcript: ...)` group when an in-process capture (Hermes) has no
transcript file. That token is no path, so a plain `not exists(token)` marks
every such failure lost, and it silently leaves health: nothing ever existed to
come back.

The check therefore requires an absolute path first. A token that is not a path
keeps today's class (`unrecoverable`, still outstanding). If the placeholder is
ever replaced by a real field, drop the `is_absolute` guard and say so in the
test `test_hermes_without_a_transcript_path_is_counted_not_healable`.

Second coupling: tests that build a refused admission or a failure with a fake
`/t/x.jsonl` path now read as lost. A fixture meant to stay outstanding must
write the transcript file.
