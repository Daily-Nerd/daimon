---
id: 50
type: deadend
title: Forcing data-theme via setAttribute does not re-render Docusaurus ThemedImage — screenshots then show phantom missing-logo bugs
severity: medium
confidence: 0.9
created: 2026-08-07
authors: ["claude-code", "Kibukx"]
anchors:
  - path: website/docusaurus.config.ts
  - path: website/src/css/custom.css
evidence:
  - note: 2026-08-06 blind design review: 'light-mode wordmark missing' filed as CRITICAL in three consecutive rounds; refuted by emulating colorScheme light + clean reload — navbar img renders 139x32
expires:
  condition: "site stops using ThemedImage/srcDark for the navbar logo"
  review_after: 2027-02-01
status: active
---

To capture light/dark screenshots of the Docusaurus site, setting
`document.documentElement.setAttribute('data-theme', 'light')` flips CSS
variables but NOT React state: ThemedImage keeps only the variant node it
mounted for the ORIGINAL theme, so the navbar logo (and any srcDark asset)
renders as a zero-width hole in the forced theme. Three independent blind
design reviewers filed "light-mode wordmark missing" as CRITICAL from such
screenshots; a fix (min-width reservation) was even committed and later
reverted. The truth: emulate the color scheme at the browser level
(prefers-color-scheme), clear the `theme` localStorage key, and RELOAD before
capturing — then the correct themed node mounts and the logo measures 139x32.
Never diagnose theme-dependent asset bugs from attribute-forced captures.
