---
id: 0
type: fence
title: The recall index is built by judging each row before insert, so deleting rows afterwards (FTS 'delete', DELETE) is the wrong repair and would leave the value in free pages
severity: high
confidence: 0.85
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/recall.py
  - pattern: "INSERT INTO items_fts\(items_fts, rowid"
evidence:
  - note: "#1132 PR 9a: tests/test_recall_judged.py pins that no withheld byte reaches the index file, including after an upgrade from a schema-9 index; tests/test_read_sentinel.py scans the file bytes"
expires:
  condition: "the index stops being a sqlite file the process writes (a different store, or an in-memory index)"
  review_after: 2027-04-07
status: candidate
---

A row that is inserted and then deleted is still in the file: the page it sat
on goes to the free list with its bytes intact, and the contentless FTS table
keeps the tokens it indexed. Scrubbing after the build (the old forgotten
scrub and quarantine pass) therefore proved only that a query could not find
the value, not that the file no longer held it.

`rebuild` now classifies every scanned item through `view.judge(bucket)`
before the INSERT. A withheld item has no row and no FTS entry, and it still
takes part in supersession in memory (so an ambiguity count does not shrink
because a twin is withheld). Never add a post-insert delete pass; if a new
reason to withhold appears, put it in `view.classify` and the judge picks it up.

If a query finds a row the judge drops, the index is behind the ledgers: the
fix is a rebuild (once per 30 s per index path), not a delete. A mark whose
cause is a withheld value (a superseder, a `superseded-by:` resolution) is
written with the generic label `resolved`, never the session or item id.
