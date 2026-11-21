---
id: 25
type: landmine
title: "a new event kind on a real item ref still hides the item from an older daimon that folds kind-blind"
severity: critical
confidence: 1.0
created: 2026-07-28
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/capture.py
  - path: plugin/daimon_briefing/cli/
  - path: plugin/daimon_briefing/recall.py
  - path: plugin/daimon_briefing/briefing.py
  - pattern: "append_event[(][^)]{0,200}kind="
violation: "append_event\([^)]{0,200}kind=(?!["'](tombstone|corroboration|handoff)["']|args\.kind)"
evidence:
  - note: 2026-07-28 probe while designing #376: append_event(ref, 'quote-verification-failed', kind='verification') then resolutions() then is_resolved() returned True — the item would vanish from briefing, recall and carry
  - note: "2026-08-02 whitelist amendment (#523): kind='handoff' added — the baton is REF-LESS by contract (item_ref always \"\"), and the resolutions fold ignores ref-less lines, so a handoff event cannot resolve anything; guarded by test_handoff_event_never_resolves_an_item in test_store.py"
expires:
  condition: "no daimon older than the release that added LIFECYCLE_KINDS (#1363) can read a store this version writes"
  review_after: 2027-07-28
status: active
---

Since #1363 the CURRENT fold is safe: `store._fold` reads lifecycle rows only
(`store.LIFECYCLE_KINDS`: no kind, `resolution`, and the forget pair
`tombstone` and `lift`), so a row of any other kind, known or unknown, is
inert and the item stays visible. `is_resolved()` still resolves an unknown
status on a lifecycle row, as designed.

The trap that remains is the OLDER reader. Before #1363, `resolutions()` folded
strictly on `item_ref` and never looked at `kind`, and `is_resolved()` resolved
every status except `reopen*`, `supersede-candidate*` and `corroborat*`. A
daimon of that vintage reading the same store (a teammate's clone, an
un-upgraded host) still treats a new kind written on a real `item_ref` as a
resolution: the item disappears from its briefing, carry and recall.

Original probe, 2026-07-28, while designing the #376 rejection ledger:
`append_event(ref, 'quote-verification-failed', kind='verification')` then
`resolutions()` then `is_resolved()` returned True. That design would have
hidden every item verification downgraded, the inverse of the intent.

Rule while old readers can exist: a non-lifecycle event must keep an `item_ref`
no item id can equal, either empty (the handoff baton) or namespaced
(`corroboration:<id>`), or live in its own stream (`verification.jsonl`,
`forget-hits.jsonl`). Do not rely on `kind` to isolate a row from an old
reader, and do not add a kind to `LIFECYCLE_KINDS` unless it really is a
lifecycle fact: listing it makes the fold read it.
