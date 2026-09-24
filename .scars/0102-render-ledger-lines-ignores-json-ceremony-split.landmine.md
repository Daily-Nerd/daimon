---
id: 102
type: landmine
title: render_ledger_lines always writes stdout — a --json ceremony must hand-print its lines to route them to stderr
severity: medium
confidence: 0.85
created: 2026-09-23
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: git-config-interactive
anchors:
  - path: plugin/daimon_briefing/cli/_ledger.py
  - path: plugin/daimon_briefing/cli/ruling.py
  - path: plugin/daimon_briefing/render.py
evidence:
  - note: issue #1094: propose --ratify ceremony first draft used render.render_ledger_lines() unconditionally; tests/test_ruling_policy_cli.py::test_revise_no_request_policy_clears_an_active_grant and test_show_names_a_pending_proposal_that_carries_a_request_policy (both pass --json and json.loads() the captured stdout) failed because the ceremony text landed on stdout ahead of the JSON, corrupting it
expires:
  condition: "render_ledger_lines grows a file= parameter that ceremony callers can point at stderr"
  review_after: 2027-03-01
status: active
---

`render.render_ledger_lines` (plugin/daimon_briefing/render.py) has no `file=`
parameter — its plain-mode branch is a bare `print(ln)` loop, always to
stdout, regardless of caller context. `ruling ratify`'s existing ceremony
already knows this: it picks `ceremony = sys.stderr if args.json else
sys.stdout` and only calls `render_ledger_lines`/`_print_ruling` on the
`not args.json` branch, hand-printing a plain line to the chosen stream
under `--json`. A new ceremony added to a DIFFERENT verb (here, `propose
--ratify`, #1094) that calls `render_ledger_lines` unconditionally silently
breaks every `--json` caller that treats stdout as parseable — the ceremony
prose lands before the JSON object on the same stream. There is no crash and
no type error; the tests that catch it are the ones that `json.loads()` the
captured stdout. Any new write-verb ceremony must copy `ruling ratify`'s
stderr/stdout split verbatim (pick the stream once, branch `render_ledger_
lines` vs. a hand-printed line on `args.json`, route every ceremony `print`
through that stream) rather than assuming `render_ledger_lines` is
JSON-safe by default.
