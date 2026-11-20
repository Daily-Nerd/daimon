---
id: 24
type: landmine
title: In the es build, .md-style doc links break whenever source and target sit on opposite sides of the translated/untranslated line
severity: medium
confidence: 0.9
created: 2026-07-18
authors: ["claude-code", "Kibukx"]
anchors:
  - path: website/docs/
  - path: website/i18n/
evidence:
  - pr: 334 (runs 29661022607 and 29661156116 — two failures, one from each direction, same evening)
  - note: "firing-review 2026-09-08: 21 fires since the last revision, concentrated on website/docs/reference/cli.md and website/docs/hosts/claude-code.md plus their es mirrors under website/i18n/; the whole docs/i18n tree is the correct risk surface for this locale-fallback hazard, left unchanged"
  - note: "firing-review 2026-09-24: re-verified, hazard live, anchors on target"
expires:
  condition: "the site moves off .md-relative doc links entirely (URL-style everywhere), or i18n gains a build-time guarantee that no locale can fall back. NOTE: 100% es coverage does NOT retire this scar — coverage was already 15/15 when it was promoted, and the hazard returns the moment one untranslated page is added."
  review_after: 2027-02-19
status: active
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
heading that only exists in the other locale used to be a build WARNING; it
is a build FAILURE now (onBrokenAnchors: 'throw' since #771,
website/docusaurus.config.ts:33), so a fragment crossing the line needs a
URL-style link as well.

Coverage note (re-verified 2026-11-19): es translation is currently complete,
46/46 docs pages (identical file sets under website/docs and
website/i18n/es/.../current), so no fallback page exists and the hazard is
DORMANT, not gone. Do not read "no untranslated pages" as evidence this scar
is stale. plugin/tests/test_docs_links.py checks each tree on its own, so it
does not model the mixed fallback map. The hazard fires the moment a 47th
English page lands without its es copy, which is the
exact moment someone is adding docs and least likely to be thinking about the
locale build.
