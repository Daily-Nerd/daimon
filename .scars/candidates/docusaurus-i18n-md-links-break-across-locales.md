---
id: 0
type: landmine
title: In the es build, .md-style doc links break whenever source and target sit on opposite sides of the translated/untranslated line
severity: medium
confidence: 0.9
created: 2026-07-18
authors: ["claude-code"]
anchors:
  - path: website/docs/
  - path: website/i18n/
evidence:
  - pr: "334 (runs 29661022607 and 29661156116 — two failures, one from each direction, same evening)"
expires:
  condition: "every doc page is translated in i18n/es (no fallback pages left), or the site moves off .md-relative links entirely"
  review_after: 2026-10-01
---

Docusaurus resolves `[text](../x/y.md)` links against the set of source files
the docs plugin loaded *for that locale* — for `es`, that is the translated
copy when one exists, else the English fallback. Consequence, hit twice in
PR #334: (1) a translated es page linking an untranslated doc by `.md` path
fails (the file is not in `i18n/es/`); (2) an untranslated English page
linking a *translated* doc by `.md` path also fails in the es build (the
English file was replaced by the es copy in the plugin's map). Both throw at
build time via `onBrokenLinks: 'throw'`, but only in the es locale pass.
Rule: `.md`-relative links are safe only between pages on the SAME side of
the translated/untranslated line. Anywhere the line is crossed, use URL-style
links (no `.md`; category-index pages like `hosts/index.md` and
`team/team.md` are `../hosts/`, `../team/`). Do NOT try to fix cross-locale
anchors with `{#custom-id}` heading syntax — in this site's MDX pipeline the
brace block is parsed as a JSX expression and acorn fails the whole es build
(third #334 failure, run of 2026-07-18T21:22Z). A fragment pointing at a
heading that only exists in the other locale is a build WARNING
(onBrokenAnchors default), which is acceptable; a broken page link is not.
