---
id: 0
type: landmine
title: A shape check at the partial boundary turns one bad chunk into a lost run unless the fan-out owns the failure
severity: high
confidence: 0.8
created: 2026-11-22
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/serializer.py
  - pattern: "_settle_extraction"
evidence:
  - commit: a10f1f3
expires:
  condition: "partial_reason stops rejecting, or the fan-out stops raising OutputParseError per call"
  review_after: 2027-05-22
status: candidate
---

Since #1218 a flat partial (open_questions at the top level) is rejected at the
parse boundary. Before it, flat partials flowed into the merge, which often
repaired them. After it, every such output raised OutputParseError out of the
whole chunk or perspective fan-out: 148 logged failures from 2026-10-21, crashed
sessions that heal could not recover, because heal re-runs the same fan-out.
Flat output is not specific to perspective prompts; plain chunks fail the same
way and the prompt schema is correctly nested.

The fan-out (_one_chunk, _one_pass) now turns that error into a dropped call,
and _settle_extraction raises only when nothing survives. Do not move the
tolerance into partial_reason (no wrapping, no defaults, #1218 is deliberate)
and do not swallow it silently: the drop is logged per call and not cached.
Merge-group failures are still fatal; a merged group has no sibling to stand in.
