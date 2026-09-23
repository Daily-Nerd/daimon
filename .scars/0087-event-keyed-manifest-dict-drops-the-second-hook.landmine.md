---
id: 87
type: landmine
title: A hook manifest keyed by event silently drops the second hook on that event, and every assertion still passes
severity: high
confidence: 0.9
created: 2026-09-14
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: hook/codex-hooks.py
  - path: plugin/daimon_briefing/codex_hooks.py
  - path: hooks/hooks.json
evidence:
  - note: #1031, registering a second PreToolUse hook beside daimon-pre-action.py
expires:
  condition: "the manifests stop being lists of {script, event, entry} rows, or a schema forbids two rows on one event"
  review_after: 2027-03-14
violation: "h..event..: h for h in"
status: active
---

Several registration tests reduced a HOOKS manifest to `{h["event"]: h for h
in HOOKS}` and then asserted about `hooks["PreToolUse"]`. That worked only
while every event carried exactly one hook. #1031 added a second PreToolUse
registration, and the comprehension kept the LAST row for that key.

Two different failures come out of the same line, and only one of them is
loud. `test_both_codex_manifests_register_the_pre_action_hook` started
asserting about the new hook and failed, which is fine, that is a test doing
its job. `test_both_hook_manifests_agree` in test_codex_session_end.py kept
PASSING: both sides collapsed identically, so the compare stayed equal while
the pre-action entry quietly left the comparison altogether. A parity test
that no longer compares the thing it was written for reports green forever.

The same shape sits in `hooks/hooks.json` assertions that index
`cfg["PreToolUse"][0]`, which is correct only as long as nobody reorders.

Key these by SCRIPT, not by event, and assert the event as a field. When an
index into a list is unavoidable, assert what that index holds by name in the
same test, so a reorder fails instead of silently retargeting.
