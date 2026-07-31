---
id: 25
type: landmine
title: "append_event with ANY kind hides the item — resolutions() ignores kind and is_resolved() defaults unknown statuses to resolved"
severity: critical
confidence: 1.0
created: 2026-07-28
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/capture.py
  - path: plugin/daimon_briefing/cli.py
  - path: plugin/daimon_briefing/recall.py
  - path: plugin/daimon_briefing/briefing.py
  - path: plugin/tests/
  - pattern: "append_event[(][^)]{0,200}kind="
violation: "append_event\([^)]{0,200}kind=(?!["'](tombstone|corroboration)["']|args\.kind)"
evidence:
  - note: 2026-07-28 probe while designing #376: append_event(ref, 'quote-verification-failed', kind='verification') then resolutions() then is_resolved() returned True — the item would vanish from briefing, recall and carry
expires:
  condition: "resolutions() filters by kind (or the ledger moves to its own stream) AND is_resolved() no longer treats unknown statuses as resolved"
  review_after: 2027-07-28
status: active
---

`store.append_event` takes a `kind` argument, which reads as if it namespaces
the event. It does not. `resolutions()` folds strictly on `item_ref` and never
looks at `kind`, so the newest event for a ref wins whatever kind it carries.
`is_resolved()` then applies a deliberate default: only `reopen*` and
`supersede-candidate*` keep an item live, and "unknown statuses resolve
(the writer bothered to record a lifecycle fact) rather than vanish".

Combined: writing ANY new event kind against a real `item_ref` silently
RESOLVES that item. Three readers act on it — `briefing.py:214`,
`cli.py:300`, `recall.py:404` — so the item disappears from the briefing,
from carry, and from recall.

Probed 2026-07-28 while designing the #376 rejection ledger, whose first
design was "reuse the existing event trail, do not invent a second one".
That design would have hidden every item verification downgraded, which is
the exact inverse of the intent: a downgraded item must stay visible and
merely be relabelled inferred.

Why this has stayed invisible: `kind` has exactly ONE non-default use today,
`kind="tombstone"` for `daimon forget` (`tests/test_recall.py:1502`), and
that test asserts the item DISAPPEARS from recall. For tombstone, resolving
is the intended effect, so the coupling has never once been wrong in
practice and reads like deliberate design.

Before adding any non-lifecycle event kind: either give it its own stream,
or teach `resolutions()` to filter by kind FIRST and re-check all three
readers. Do not assume `kind` isolates anything.
