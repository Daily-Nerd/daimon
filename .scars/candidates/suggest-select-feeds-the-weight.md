---
id: 0
type: landmine
title: A column _suggest_weight reads must also be SELECTed in suggest() itself; search() selecting it proves nothing
severity: high
confidence: 0.9
created: 2026-09-02
authors: ["claude-code"]
anchors:
  - pattern: "_suggest_weight"
evidence:
  - note: "#837 (2abf0c4): invalidated_by was written and selected in search() but not in suggest()'s own SELECT; the auto-inject path re-asserted contradicted claims. The fix left the comment 'the column MUST be selected here, not just written' at recall.py suggest()"
  - note: "#907 (2026-09-02): superseded_source, selected in search() since #867, was absent from suggest()'s SELECT. A weight-level test on a dict passed; the end-to-end suggest test was red with KeyError: 'superseded_source'. Every human resolution would have demoted at the model-link rate"
expires:
  condition: "search() and suggest() share one column list (a single SELECT builder), so a column cannot be present in one and absent from the other"
  review_after: 2027-03-02
status: candidate
---

`recall.search()` and `recall.suggest()` build SEPARATE SELECT statements over the
same `items` table. `_suggest_weight(row, ...)` ranks whatever dict suggest()
hands it, and reads columns by `row.get(...)`, which returns None for a column the
SELECT never named. So a new rank input that is written at rebuild, selected in
search(), and read in `_suggest_weight` is silently dead on the suggest path: no
error, no test failure at the weight level, the auto-inject surface just ranks as
if the column were empty. Twice now (#837 invalidated_by, #907 superseded_source).

When you add or start reading a column in `_suggest_weight`: (1) add it to
suggest()'s SELECT too, next to the #837 comment; (2) write the proving test
THROUGH `recall.suggest()` with two rows equal on relevance, importance and
first_seen so only the new column differs; a dict-level test of `_suggest_weight`
cannot catch the missing wire. Do not treat search() selecting the column as
evidence that suggest() does.
