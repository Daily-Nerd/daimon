---
id: 51
type: deadend
title: Shrinking the landing hero on mobile drops it below the 2x-body hierarchy floor
severity: low
confidence: 0.8
created: 2026-08-06
authors: ["claude-code", "Kibukx"]
anchors:
  - path: website/src/css/custom.css
violation: "hero--daimon h1 [{][^}]*font-size: 1[.]"
evidence:
  - commit: abbfe08
  - pr: 615
expires:
  condition: "landing h1 becomes a long multi-word headline that genuinely cannot fit at 36px on 320px screens"
  review_after: 2027-02-01
status: active
---

Tried the reflex mobile pattern: shrink the hero h1 to 1.875rem (30px) under
640px (PR #615). That breaks the deliberate type hierarchy — hero must
stay at 2.0-2.44x body (36px / 16px = 2.25x); 30px is 1.88x and the page loses
its single dominant element exactly where screens are smallest. The title is
the six-character word "daimon"; 36px fits every viewport down to 320px, so
the shrink solved nothing. Reverted in the same PR: the mobile media query adjusts
hero PADDING only, never the h1 font-size. If the h1 ever needs a mobile size,
pick from {36, 48} or restructure the copy — do not slide below 2x body.
