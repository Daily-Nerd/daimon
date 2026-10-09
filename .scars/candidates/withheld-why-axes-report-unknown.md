---
id: 0
type: deadend
title: why cannot keep the textless evidence axes of a withheld item, because the view hands out no item for a Withheld and the receipt lives in the item
severity: medium
confidence: 0.7
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/inspector.py
  - pattern: "def _withheld_result\("
evidence:
  - note: "#1132 PR 11a: the design wanted capture, provenance, locator, bytes and verifier kept for a withheld item (#1070, H13). _withheld_result is built from the Withheld and the lineage only, and neither carries the receipt; tests/test_why_contract.py pins the unknown values"
expires:
  condition: "view.Withheld or view.Appearance carries a textless projection of the item's receipt, or a person rules the axes may go"
  review_after: 2027-04-09
status: candidate
---

`why` for a withheld item reports `capture: unknown`, `provenance:
legacy-unbound`, `locator: unsupported`, `bytes: unknown`, `verifier_comparison:
unknown` and `current_support: withheld`. That is not a bug in the lookup. The
axes are computed from the item's `quote_provenance` receipt, and a `Withheld`
deliberately carries no item (the pointer-window filter deletes it from the
copy it hands out). Reading the receipt from the raw pointer would let the item
cross the view, which is the thing the view exists to prevent.

If a person decides the axes matter for a quarantined item, the way out is a
small projection built inside the view (receipt outcome, verifier id, source
locator: no text, no quote) carried on `Withheld` or on the lineage's
appearances, not a raw read in the inspector.
