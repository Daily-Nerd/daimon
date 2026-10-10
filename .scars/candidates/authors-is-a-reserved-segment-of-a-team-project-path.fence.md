---
id: 0
type: fence
title: "authors" is a reserved segment of a logical team project path, so one walker finds author directories
severity: medium
confidence: 0.85
created: 2026-10-09
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/teamproject.py
  - pattern: "RESERVED_SEGMENT"
evidence:
  - commit: 84b5485
  - note: tests/test_teamproject.py::test_env_path_with_the_reserved_segment_never_resolves
expires:
  condition: "the sidecar layout names its author level with something a project path cannot contain"
  review_after: 2027-04-09
status: candidate
---

The sidecar holds checkpoints and published ledgers at `authors/<a>/` and
`projects/<segments>/authors/<a>/`. `store._team_author_dirs` finds author
directories by looking for a directory named `authors`. A logical project path
that itself contained an `authors` segment (`squad/authors/census`) would make
that walk read a project directory as an author directory, so a project path
could pose as an author and two real authors' files could be misattributed.

Rather than teach every walker, `authors` is reserved: `team init` refuses it,
the resolver drops a path that holds it, and a `[projects."..."]` table that
uses it is skipped and reported. Every reader of a team ledger (tombstones, the
forgotten stamp, the recall fingerprint, the census, the sync pathspecs) goes
through `store._team_ledger_paths` on that one walker. Do not add a second
`rglob` for a team file.
