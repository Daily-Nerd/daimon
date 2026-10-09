---
id: 0
type: landmine
title: "A read verb that gates on the human channel must test stdin.isatty itself, because _human_cli_channel prints its refusal to stdout and corrupts --json"
severity: medium
confidence: 0.9
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/trust.py
  - path: plugin/daimon_briefing/cli/lifecycle.py
evidence:
  - commit: 8570619
  - note: "tests/test_trust_show_channel.py::test_json_carries_the_evidence_to_a_tty_and_omits_it_otherwise"
expires:
  condition: "_human_cli_channel gains a quiet mode that prints nothing"
  review_after: 2027-04-09
status: candidate
---

`lifecycle._human_cli_channel(verb)` answers the channel, but on a non-interactive caller it also prints
"<verb> is a human verdict and requires an interactive terminal" to stdout and bumps a usage counter. That is
right for a write verb that is refused. `trust show` and `trust list` are reads that give an agent LESS (a
count instead of the evidence), not a refusal, and they emit `--json`: one printed line before the array makes
stdout unparseable.

`cli/trust.py::_human_channel` therefore reads `sys.stdin.isatty()` directly. The census already treats the
two the same (a tty is the human variant). Do not swap it for `_human_cli_channel` to reuse code.
