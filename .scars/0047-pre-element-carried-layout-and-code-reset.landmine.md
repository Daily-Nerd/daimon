---
id: 47
type: landmine
title: Replacing pre/code elements for nesting fixes silently drops the UA and Infima styles they carried
severity: medium
confidence: 0.85
created: 2026-08-06
authors: ["claude-code", "Kibukx"]
anchors:
  - path: website/src/pages/index.tsx
  - path: website/src/css/custom.css
evidence:
  - commit: 2c35964
  - pr: 627
expires:
  condition: "installBlock markup stops using code elements inside a non-pre wrapper"
  review_after: 2027-02-01
status: active
---

PR #627 swapped the install block's outer element from pre to div to fix
invalid div-inside-code nesting. Correct fix, but the pre was silently doing two
other jobs: Infima's "pre code" reset (no border, no background, font-size 100%)
and, before that, an inline-block wrapper constrained the block's width. Both
were lost — bare code elements regressed to grey bordered chips at ~12.6px
(90% code-font var compounding on 0.875rem) and the block went full-bleed at
wide viewports. Neither showed at 320px, so narrow-viewport rendering checks
passed. Restored explicitly later in the same PR (.installBlock inline-block +
.installBlock code reset). When you change a semantic element here, grep what
the UA sheet and infima/dist/css/default/default.css attach to it, and re-check
at BOTH narrow and wide viewports.
