---
id: 0
type: deadend
title: Denying an unknown project inside store._scoped_read for every route breaks status, receipts and the persist path
severity: medium
confidence: 0.85
created: 2026-10-21
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - pattern: "def _scoped_read"
evidence:
  - note: #1223 step 1 tried "slug is None reads nothing on both routes" in _scoped_read; test_receipts.py status_line tests (signed, marked-but-missing, predates, no-transcript), test_hooks.py::test_on_session_end_writes_checkpoint and read_own_stream_latest all failed, none of them about injection
expires:
  condition: "the CLI status and receipts lines stop calling read_latest_body with Route.OWN_ELSE_GLOBAL for an identity-less process, and read_own_stream_latest no longer needs the global pointer"
  review_after: 2027-04-21
status: candidate
---

The briefing-injection leak for a project with no identity is closed where the
decision is made: `hooks.pre_llm_call` asks `view.read_scope` and stops on
`Denied`, and `briefing.injection_read_route` no longer maps a None project to
`Route.OWN_ELSE_GLOBAL`. The same denial was first tried one layer lower, in
`store._scoped_read`, so that no route could return the global pointer for an
unknown project. That is wrong. `receipts.status_line`, `cli/status.py` and
`cli/brief.py` choose `Route.OWN_ELSE_GLOBAL` on purpose, and the persist path
(`read_own_stream_latest`, #126) needs the global pointer because it IS that
stream's previous checkpoint. Denying there broke all of them, in tests that
have nothing to do with injection.

If the store is to refuse an unknown project on display routes, split the
persist read out of `_scoped_read` first, and decide the status and receipts
lines on their own. Do not add the denial to the shared reader alone.
