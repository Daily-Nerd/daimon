---
id: 46
type: landmine
title: A host adapter's spawn-line session id and its checkpoint file name must resolve to the same ledger key
severity: high
confidence: 0.9
created: 2026-08-08
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/ledger.py
evidence:
  - pr: 642
  - issue: 634
expires:
  condition: "the ledger keys sessions by an explicit id carried on every result line, instead of deriving it from a file stem"
  review_after: 2027-02-08
status: active
---

`_session_ledger` derives a session key two different ways: spawn lines give it
the id the HOOK logged, success/error lines give it the STEM of the checkpoint
or transcript path. Those are the same string for Claude and Gemini, so the
coupling is invisible — until a host names its files differently.

Codex does. It spawn-logs the bare session id but names both the rollout
transcript and the checkpoint `rollout-<stamp>-<id>`, so spawn and result folded
under different keys. Every SUCCESSFUL Codex capture therefore also produced a
phantom "spawned, no result (hung/killed)" failure in `daimon status`, and once
Codex rotated the transcript the phantom stuck forever as "not auto-repairable"
(#634, fixed in #642 by `_session_key`).

Adding a host adapter: if its checkpoint or transcript file name is not exactly
the id its hook writes on the spawn line, extend `_session_key` — do NOT assume
`Path(p).stem` is a session id. The failure is silent and inverted: status
reports healthy captures as losses, so the gauge reads WORSE than reality and
nobody suspects a parsing bug. `_has_checkpoint` carries the same assumption on
the probe side and needs the same treatment.
