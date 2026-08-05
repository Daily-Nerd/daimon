---
id: 0
type: landmine
title: A writer that forges an absent key destroys the sentinel a fold reads as replacement intent
severity: high
confidence: 0.9
created: 2026-08-05
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/refutations.py
evidence:
  - pr: 575
  - note: "Reproduced in isolated DAIMON_CHECKPOINT_DIR by two independent reviewers; the repo's own suite executed the bug and passed."
expires:
  condition: "append() and fold() no longer split the meaning of a key between 'absent = unchanged' and 'present = replace'"
  review_after: 2027-02-05
status: candidate
---

`append()` normalised every row with an unconditional
`row["anchors"] = _scrub_list(list(row.get("anchors") or []))`, which forges the
key as `[]` when the caller never set it. `fold()`'s `revised` branch decides
replacement by key PRESENCE (`if "anchors" in row`), and `revise()` uses
`is not None` to mean "unchanged". The writer therefore destroyed the sentinel
before the fold could read it: any revision that did not pass `--anchor` cleared
every anchor, so `guard()` stopped matching while `refute show` still rendered
`[✗ active · human-ratified]`. A guard that reads as armed and is not.

Two rules for anyone touching a ledger writer here. Normalise only keys the
caller actually set — `if key in row:` — because in an append-only stream the
absence of a key is data. And never let a writer and a fold disagree about what
absence means: if the fold reads presence as intent, the writer must not
manufacture presence.

This is the second silent-suppression fold bug in this repo (scar 0025 was the
`events.jsonl` resolution fold ignoring event kind). Both had green CI. Patch
coverage does not catch this class: every line of `fold()` executed, on the
wrong data, and `test_write_audit_guard.py` drove the exact failing sequence
while asserting only `rc == 0` — which `_cmd_refute_guard` also returns on zero
matches. When you touch a fold, assert on the FOLDED STATE, never on exit code.
