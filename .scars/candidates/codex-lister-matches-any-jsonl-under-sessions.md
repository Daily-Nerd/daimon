---
id: 0
type: fence
title: The Codex transcript lister matches any .jsonl under sessions, never only rollout-*, because the receipt index reads the same list
severity: medium
confidence: 0.85
created: 2026-10-27
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/provenance.py
  - path: plugin/daimon_briefing/importer.py
  - pattern: "def transcripts\b"
  - pattern: "\*\*/\*\.jsonl"
evidence:
  - commit: 3e68cbf
  - note: "#1236 design section 7 names the Codex root as sessions/**/rollout-*.jsonl; tests/test_provenance.py::test_resolver_supports_codex_recursive_sessions resolves a receipt to sessions/2026/08/S-codex.jsonl, a stem with no rollout prefix"
expires:
  condition: "SourceResolver._candidates stops building its Codex index from SourceResolver.transcripts, or the receipt resolver gains its own Codex pattern"
  review_after: 2027-04-27
status: candidate
---

`SourceResolver.transcripts("codex")` globs `*.jsonl` below `sessions` and
`archived_sessions`. The #1236 design writes the Codex root as
`sessions/**/rollout-*.jsonl`, and a reader of the design will want to
narrow the pattern to match it. Do not.

The same list is the input to the stem index `_candidates` builds for
resolving quote receipts (the one index since 3e68cbf, so that what the
import can discover and what a receipt can resolve to are one set). That
index has always matched any `.jsonl` under the Codex roots, and
`test_resolver_supports_codex_recursive_sessions` pins it with a file named
`S-codex.jsonl`: a checkpoint whose `source_ref.session_id` is a bare stem
must keep resolving. Narrowing the lister to `rollout-*` would turn such a
receipt from `resolved` into `absent-local` with no error anywhere.

For discovery the wider pattern is harmless: a non-rollout file under the
Codex roots is imported only when the first row that records a `cwd` (the
`session_meta` row on a rollout) names this project, and its session key
is its stem, which the ledger already folds correctly (only a `rollout-`
stem is rewritten to the bare id). If a narrower discovery pattern is ever
wanted, give discovery its own filter over the lister's output; do not
change the lister.
