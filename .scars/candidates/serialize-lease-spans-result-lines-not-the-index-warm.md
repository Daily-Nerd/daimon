---
id: 0
type: fence
title: The serialize heartbeat lease must cover every result line and end before recall.warm()
severity: medium
confidence: 0.8
created: 2026-10-15
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/serialize.py
  - path: plugin/daimon_briefing/hooks.py
evidence:
  - commit: 7e6785e
  - commit: 56c9c4f
expires:
  condition: "a single completion record replaces the stamp-plus-log-line pair as the liveness source"
  review_after: 2027-04-15
status: candidate
---

The heartbeat stamp is deleted when a serialize ends, and a host reads "finished"
from the result line in serialize.log. The old `finally: clear_heartbeat` ran
BEFORE the kill-switch skip line and the success line were appended, so a reader
could see no stamp and no result for a moment and call the session gone.
`ledger.heartbeat_lease` now wraps the pipeline and every result line in both
doors, so the line always lands while the stamp exists.

Two things look movable and are not. The `with` block in `_run_serialize` must
include the success and skip appends; hoisting them out reopens the window. And
`recall.warm()` must stay AFTER the block (and `lease.close()` before it in
`hooks.on_session_end`): a rebuild can take minutes and holding the stamp
through it makes a finished capture read as running (#564). In the Hermes door
the lease also has to outlive the `except` handlers, because `_ledger_failure`
writes the failure lines there; closing it at the end of the `try` body drops
the stamp before the line that explains why.
