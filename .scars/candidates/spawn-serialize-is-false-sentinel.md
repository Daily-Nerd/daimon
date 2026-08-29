---
id: 0
type: fence
title: spawn_serialize callers test `is False`, not falsiness, because ~16 test fakes return None
severity: medium
confidence: 0.85
created: 2026-08-29
authors: ["claude-code"]
anchors:
  - path: hook/
  - path: plugin/daimon_briefing/_hooks/
  - pattern: "spawn_serialize\\(.*\\) is False|lib\\.spawn_serialize"
evidence:
  - commit: 681c234
  - pr: 814
expires:
  condition: "every `monkeypatch.setattr(lib, 'spawn_serialize', ...)` fake in plugin/tests returns an explicit bool, at which point plain falsiness is safe"
  review_after: 2027-02-28
status: candidate
---

`spawn_serialize` returns `False` when it skipped the spawn because a serialize
for that transcript is already in flight (#813). Every caller checks
`if lib.spawn_serialize(...) is False:` rather than `if not lib.spawn_serialize(...)`.

That looks like an over-careful identity comparison a linter or a tidying pass
would want to simplify. Do not simplify it.

The test suite replaces this function in roughly sixteen places with fakes
shaped `lambda cli, path, env: calls.append(path)`, which return `None`. Under
a truthiness check every one of those fakes flips its caller into the skip
branch: the hook stops logging `spawned serialize` and starts logging
`skipped serialize`, silently, in tests whose assertions are about spawn calls
rather than log lines. The `is False` sentinel makes the un-updated case keep
the OLD behaviour, so the contract change is safe by construction instead of
safe only if you found every fake. The full suite passing unchanged across the
migration is the evidence that it worked.

Same hazard class the #795 review named for `read_latest`: a return-contract
change that is SILENT at call sites which never inspect the value. There the
answer was to keep the new type off the migration path entirely; here it is a
sentinel that only an explicit value triggers.

The honest log line is the reason this matters rather than being cosmetic.
`ledger` parses `spawned serialize`, and a spawn line with no matching result
classifies as hung, which invites `heal` to retry work that never started.
