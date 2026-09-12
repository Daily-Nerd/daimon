---
id: 0
type: landmine
title: Appending a stats --json section breaks a tail assertion in test_checks_surfaces.py, a file about rulings
severity: medium
confidence: 0.9
created: 2026-09-12
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/__init__.py
  - path: plugin/tests/test_checks_surfaces.py
evidence:
  - note: "#974 step 1 — adding the `stitching` section, exactly as the in-code comment instructs, failed test_stats_json_carries_checks_at_the_tail"
expires:
  condition: "the tail assertion is rewritten to pin the appended ORDER of all sections rather than whichever one happens to be last"
  review_after: 2027-03-01
status: candidate
---

`_cmd_stats` carries a comment saying `stats --json` key order is a contract
and a new fact is APPENDED at the tail. Following that instruction breaks
`test_stats_json_carries_checks_at_the_tail`, which asserts
`list(payload)[-1] == "checks"`. The assertion does not pin the append-at-tail
rule, it pins one particular section as permanently last, so it fires against
the very change the contract asks for.

The trap is where it lives. The failure surfaces in
`plugin/tests/test_checks_surfaces.py`, a file about armed rulings and check
firings, with nothing in its name or module docstring connecting it to the
stats payload. Someone adding a stats section reads `_cmd_stats`, follows the
comment, runs their own new test file green, and only discovers the breakage
in a full-suite run. The same shape sits in `tests/test_cli.py`, where
`list(cap)[-1] == "gates"` pins the tail of the `capture` sub-block.

What to do instead: keep appending at the tail, and when this fires, widen the
assertion to the pair rather than weakening it to a membership check. `#974`
changed it to `list(payload)[-2:] == ["checks", "stitching"]`, which is the
form `test_checks_surfaces.py` already uses for `status --json`
(`list(payload)[-2:] == ["checks", "identity"]`). Never delete the assertion:
it is the only thing keeping a new section from being inserted into the middle
of the documented order.
