---
id: 0
type: landmine
title: A published released row lifts a quarantine, and nothing authenticates who appended it to the owner's file
severity: high
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/policy.py
  - pattern: "fold_published_quarantines"
evidence:
  - commit: a891ae3
  - note: tests/test_team_quarantine_worlds.py::test_a_planted_release_lifts_the_claim_by_design
expires:
  condition: "published team rows carry a verified signature (the version 2 row with sig) and the fold skips an unsigned or badly signed one"
  review_after: 2027-04-09
status: candidate
---

A teammate's quarantine reaches a reader as hash-only rows in
`authors/<owner>/quarantines.jsonl`, and the fold honours a `released` row. The
sidecar has no per-path access control and the row's `author` is declared, so
anyone who can write that file can lift the owner's claim by appending a
released row, or by deleting lines (which already re-exposes a forgotten value
for tombstones). The channel check that makes a local release human-only runs
on the publisher's machine and cannot be re-run by a reader.

This is the stated boundary, not a bug to fix in the fold: sidecar write access
is the trust boundary, as for checkpoints and tombstones. Do not add a reader
check that pretends to verify a row (a `channel` or `authority` field is filled
in by a forger the same way). The hygiene warning in teamsync only makes a
plain edit visible. The designed cure is signed rows with keys declared in
`daimon-team.toml`, and the fold already keys by author directory, so it can
skip an unsigned author the way it skips an unproven one.
