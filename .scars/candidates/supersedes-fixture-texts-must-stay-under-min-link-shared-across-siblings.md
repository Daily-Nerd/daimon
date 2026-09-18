---
id: 0
type: landmine
title: A multi-row suggest/search fixture with a typed supersedes link silently no-ops if sibling texts share >= _MIN_LINK_SHARED terms with the link target
severity: medium
confidence: 0.9
created: 2026-09-18
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/recall.py
  - pattern: "_apply_typed_supersession"
evidence:
  - note: "#1063: a tiering fixture with four sibling rows (\"pin the wombat pipeline cache <live|superseded|invalidated|worst> row\") all shared 5 salient terms. Two separate supersedes links against this fixture both silently failed to bind — no error, no exception, just superseded_by staying None on every row. The test's ordering assertions still partially passed by coincidence (weight-only ordering), and only an explicit `superseded_by == \"...\"` assertion caught it."
expires:
  condition: "_apply_typed_supersession stops requiring an unambiguous single-text match (_MIN_LINK_SHARED) before writing superseded_by"
  review_after: 2027-03-18
status: candidate
---

`recall._apply_typed_supersession`'s free-text branch resolves a supersedes
link's target by shared salient terms: candidate rows need >= `_MIN_LINK_SHARED`
(3) terms in common with the target text, and the match is applied ONLY if
exactly one distinct text qualifies — `if len(by_text) != 1: continue` (never
guess). A fixture with several sibling rows built from one template string
("<shared prefix> <label>") easily shares 3+ terms across every sibling, not
just the intended target, so `by_text` ends up with several distinct matching
texts and the whole link is silently dropped.

The failure mode is quiet: no exception, no test failure at write time — only
a later assertion on `superseded_by` (or an ordering assertion that happens to
depend on it) fails, and it is easy to misread that failure as a bug in the
code under test rather than in the fixture.

When building a multi-row suggest/search fixture that needs a real typed
supersedes link: keep only 1-2 common terms across ALL sibling rows (enough
for whatever overlap gate the test also needs, e.g. suggest's `_MIN_OVERLAP`
of 2), and give each row one word unique to it alone, then link on the exact
text carrying that unique word. Never reuse a single template string with only
a trailing label swapped across more than two rows.
