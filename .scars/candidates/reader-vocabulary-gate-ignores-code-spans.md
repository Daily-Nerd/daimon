---
id: 0
type: landmine
title: The reader-facing vocabulary gate is line-level and does not exempt code spans; a backticked ledger key fails CI on website prose
severity: medium
confidence: 0.9
created: 2026-09-05
authors: ["claude-code"]
anchors:
  - path: README.md
  - path: website/docs/
  - path: website/blog/
  - path: website/i18n/
violation: "\b(trials?|verdicts?|judge[sd]?|judgement|judgment|jury|testimon\w*|hearsay|prosecutes?|guilty|innocent|veredictos?|juicios?|jurado|testimonios?|culpable|inocente)\b"
evidence:
  - pr: 931
  - note: "2026-09-05: a new CLI-reference section listed a ruling row's keys, one of them backticked. Local ruff, mypy and the two touched test files were green; CI failed on tests/test_reader_facing_vocabulary.py on both Python versions. The fix was to describe the field in words on both language mirrors."
expires:
  condition: "tests/test_reader_facing_vocabulary.py strips inline code spans or fenced blocks before matching, or the gate is removed"
  review_after: 2027-03-05
status: candidate
---

`plugin/tests/test_reader_facing_vocabulary.py` keeps a short list of courtroom
words out of every reader-facing surface (README, website/docs, website/blog,
website/i18n). Its docstring says code identifiers are out of scope, but the
matcher runs `EXCLUDED.finditer(line)` on raw lines: nothing strips backticks
or fences. A JSON key or CLI flag that happens to be an excluded word fails
the gate exactly like prose does, on both language mirrors, and only in CI if
you did not run that one test file locally.

When a governed page has to refer to such a field, describe it ("the rule
text") instead of naming the key, or add an ALLOWED entry to the test with a
reason; do not weaken the pattern. The violation regex above is the gate's own
term list, so this scar trips on the same edit the test would reject.
