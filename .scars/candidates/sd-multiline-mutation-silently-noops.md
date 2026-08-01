---
id: 0
type: deadend
title: sd with multiline/complex regex silently no-ops — mutation tests then "pass" against unmutated code
severity: high
confidence: 0.9
created: 2026-08-01
authors: ["claude-code"]
anchors:
  - path: plugin/
  - pattern: "sd '[^']*\n"
evidence:
  - note: "2026-08-01 session, twice in one day. (1) #475 review: sd pattern spanning 'backend = resolve_backend()...' lines did not match; the 'restored' file still held the mutant and the full suite ran 2 failures that were misread as new-test signal. (2) #480 slice-2 review: sd multiline pattern on _tie_rank did not match, tie-break test 'passed' against UNMUTATED code and the pass was nearly reported as mutation-proof. Both caught only by re-grepping for the MUTANT marker / the expected changed line before trusting the run."
expires:
  condition: "mutation edits move to a dedicated tool that fails loudly on zero replacements"
  review_after: 2027-02-01
status: candidate
---

Tried using `sd 'pattern' 'replacement' file` for quick mutation-testing edits
where the pattern spanned multiple lines or contained parens/newlines. `sd`
exits 0 whether or not anything matched, so a non-matching pattern is
indistinguishable from a successful edit — and the follow-up test run then
"verifies" code that was never mutated. A passing mutation test is evidence
ONLY if you first confirm the mutant is present (`rg MUTANT <file>` or grep
for the changed line). Abandoned sd for this use: make mutation edits with
the Edit tool (fails loudly on no-match), tag them with a `MUTANT` comment,
and grep for the tag before running the suite; grep again after restore.
