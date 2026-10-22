---
id: 0
type: fence
title: store.read_own_stream_latest reads the global pointer for an unknown project on purpose; the display reader never does
severity: medium
confidence: 0.85
created: 2026-10-21
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def read_own_stream_latest"
evidence:
  - note: "tests/test_cli_scope_owner.py::test_the_persist_read_of_an_unknown_project_keeps_the_global_pointer and test_an_identity_less_display_read_reads_nothing pin the split"
expires:
  condition: "the pre-routing legacy stream (checkpoints written before a project was known, #126) no longer exists in any supported store"
  review_after: 2027-04-21
status: candidate
---

Two reads of the latest pointer look like one and are not. `_scoped_read` is the
DISPLAY reader: a project with no slug reads nothing on either route, and the
global pointer (the newest checkpoint of any project) is never consulted on a
tenant-scoped home. `read_own_stream_latest` is the PERSIST reader: for an
unknown project the global pointer IS that stream's previous checkpoint
(pre-routing legacy, #126), so it reads the pointer file directly and takes no
policy argument.

Denying an unknown project inside `_scoped_read` while `read_own_stream_latest`
still routed through it broke the persist path (the next write lost its
predecessor). Do not merge the two again, and do not give the persist read a
route or an env switch: what carry writes must not depend on anything a caller
or an environment variable can reach.
