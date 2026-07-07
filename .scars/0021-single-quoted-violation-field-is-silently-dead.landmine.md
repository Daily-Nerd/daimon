---
id: 21
type: landmine
title: Single-quoted violation regex is silently dead — parser strips DOUBLE quotes only, lint stays green
severity: medium
confidence: 0.95
created: 2026-07-06
authors: ["claude-code", "Kibukx"]
anchors:
  - path: .scars/
evidence:
  - note: 2026-07-06: armed a violation tripwire as violation: 'len\\(\\w+\\)\\s*==\\s*\\d' (single quotes); guilty-diff `scar check --diff` returned violations: [] and `scar lint` reported 0 errors — dead tripwire, green gauge. Same regex in double quotes fired correctly. Mechanism: scar-cli 0.16.1 model.py:150 parses the field with .strip('\"') — double quotes stripped, single quotes kept as literal characters inside the regex, which then never matches
expires:
  condition: "scar-cli parser also strips single quotes, or scar lint flags a violation value wrapped in single quotes"
  review_after: 2027-07-06
violation: "violation:\s*'"
status: active
---

Refines scar #19 ("quotes stripped"): only DOUBLE quotes are stripped from a
`violation:` value. Wrap the regex in single quotes and the apostrophes stay
inside the pattern — the tripwire can never match, `scar check --diff` returns
no violation on a guilty diff, and `scar lint` still reports 0 errors. The
protection is dead while every gauge reads green. Promotion does not heal it:
the serializer re-wraps the still-quoted value in double quotes, preserving the
inner apostrophes. When authoring or reviewing `violation:` fields, use double
quotes with raw single-backslash escapes (`violation: "len\(\w+\)"`), and prove
the tripwire with a guilty AND an innocent `scar check --diff` before trusting
it. Verified against scar-cli 0.16.1 (model.py:150).
