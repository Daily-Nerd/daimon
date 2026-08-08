---
id: 42
type: landmine
title: A residue test that enumerates surfaces via the scrubber's own walk cannot fail for a missed surface class
severity: high
confidence: 0.95
created: 2026-08-07
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/tests/test_team_tombstone_propagation.py
  - path: plugin/daimon_briefing/store.py
  - pattern: "project_surfaces\("
evidence:
  - note: test_opt_in_scrubs_local_copies asserted no residue by iterating store.project_surfaces(PROJECT) — the exact set scrub_content_key walks. apply_foreign_tombstones reaches only that set, so the chunk cache, crash log, adapter transcripts, events.jsonl and own team-mirror copies all kept the plaintext while the test passed and the CLI printed success. Found in the #600 slice B design review, 2026-08-07; filed as #620.
  - note: Second instance of the class. The first shipped a forget bug to main because a passing test asserted the residue it was meant to catch (see forget-plaintext-surfaces).
expires:
  condition: "residue assertions enumerate surfaces from surfaces.SURFACES (plaintext=True) rather than from the walk under test"
  review_after: 2027-02-07
status: active
---

A deletion test must not ask the code under test which files exist. When the
assertion iterates the same enumeration the scrubber walks, the test is
tautological with respect to COVERAGE: it proves the scrubber cleaned what the
scrubber looked at, and stays green no matter how many declared plaintext
surfaces the scrubber never looked at.

This is how apply-forget shipped reporting success while reaching one of six
plaintext surface classes. `apply_foreign_tombstones` calls only
`scrub_content_key`, which walks `project_surfaces`; the test asserted absence
over `project_surfaces` too. The chunk cache, `logs/serialize-crash.log`,
`windsurf/transcripts/*.md`, `windsurf/unparsed-*.json`,
`checkpoints/{slug}/events.jsonl` and the machine's own `team/**/*.json` copies
each held the value afterwards, and nothing failed.

Write residue assertions against the DECLARATION, not the walk: derive the
expected set from `surfaces.SURFACES` filtered to `plaintext=True`, or grep the
whole `~/.daimon` tree for the canary and allow-list what is legitimately out of
scope. Then a newly declared surface makes the test fail until someone decides
whether the deleter should reach it — which is the ratchet the surface registry
exists to provide, and it does nothing if the tests route around it.
