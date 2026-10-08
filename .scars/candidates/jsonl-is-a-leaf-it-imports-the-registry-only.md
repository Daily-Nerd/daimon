---
id: 0
type: fence
title: jsonl.py imports the registry and nothing else in the package; anything it needs from above is registered into it
severity: medium
confidence: 0.85
created: 2026-10-08
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/jsonl.py
  - pattern: "set_skip_sink"
evidence:
  - note: "#1132 PR 10b: tests/test_read_layers.py::test_jsonl_and_surfaces_are_leaves and test_no_ledger_module_imports_a_reader"
expires:
  condition: "the layer table gives jsonl a reason to import a presenter or the config module"
  review_after: 2027-04-08
status: candidate
---

Every ledger sits on `jsonl`, so a convenient import there (the note wording in
`display`, the log directory in `config`) pulls a reader or the config layer
under the lowest module and turns the layer table into a cycle waiting to
happen. The cure rule (`ledger_hint`) therefore lives in `surfaces`, which
`display` re-exports, and the skipped-row usage line is written by a sink
`config` registers with `jsonl.set_skip_sink`. If a new write exit seems to need
something from above, register it from above; do not import it here.
