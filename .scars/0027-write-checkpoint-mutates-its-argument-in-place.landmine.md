---
id: 27
type: landmine
title: store.write_checkpoint mutates its checkpoint argument in place — reading the serialize() output after writing yields the gated/redacted/id-stamped version, not the extraction
severity: medium
confidence: 0.9
created: 2026-07-29
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/policy.py
evidence:
  - issue: 407
  - note: 2026-07-29 while writing the deletion-durability protocol test: asserted `_S in serialize()_output[...]` AFTER store.write_checkpoint(sid, cp) and got a red — _drop_forgotten had already removed _S from the same dict object the test held.
  - pr: 428
  - note: 2026-07-30, PR #428: the admission pipeline moved into policy.admit_checkpoint, which deliberately KEPT the in-place mutation contract (callers rely on reading the gated dict back); the trap now spans both modules.
expires:
  condition: "write_checkpoint deep-copies its argument before mutating (redaction/_drop_forgotten/_stamp_item_ids operate on a copy), or its docstring documents the in-place contract"
  review_after: 2026-12-31
status: active
---

`store.write_checkpoint(session_id, checkpoint, ...)` does NOT treat
`checkpoint` as read-only. It mutates the SAME dict object in place — since
PR #428 via `policy.admit_checkpoint` (redact → `drop_forgotten`, the #402
value-keyed forget gate that removes items → id stamping), plus store-side
`setdefault`s (`format_version`, `created`, `author`, `project_slug`,
`git_branch`, `first_seen`, `receipts`). `policy.py` keeps the in-place
contract deliberately — callers read the gated dict back after the write.

The trap for test (and caller) authors: `serializer.serialize(...)` returns a
checkpoint dict; if you then call `write_checkpoint(sid, cp)` and later read
`cp`, you are reading the POST-mutation state — forgotten items already
dropped, text already redacted, ids already stamped — not what the extractor
produced. A test asserting on the extractor's output must snapshot it
(`copy.deepcopy(cp)`) BEFORE the write, or it will silently assert against the
gated version and either go red for the wrong reason or (worse) pass
vacuously.

This is the established in-place pattern across store.py, so it is not a bug —
but nothing in the signature or a docstring warns that the argument is
consumed. The fix that surprised me was one line: deepcopy the extraction
before handing it to write_checkpoint when you need both artifacts (the
extractor output AND the on-disk result) in the same test.
