---
id: 114
type: landmine
title: A test that reuses the sentinel world must pin DAIMON_CHECKPOINT_DIR, or its surface finds no buckets and never reaches the raise point it patches
severity: medium
confidence: 0.85
created: 2026-10-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/tests/test_read_sentinel.py
evidence:
  - note: #1132 PR 7b: test_a_converted_surface_renders_nothing_when_view_open_raises first failed for cli:projects with rc 0 and 'no project buckets yet': the module-scoped world was built under the first test's home, and the autouse isolation of every later test points DAIMON_CHECKPOINT_DIR at a fresh empty one. Surfaces that raise before reading (brief, loops) passed anyway.
expires:
  condition: "the world fixture owns its environment for every test that uses it, or the autouse isolation stops redirecting the checkpoint dir"
  review_after: 2027-04-01
status: active
---

`world_run` is module-scoped: it builds the store once and the drive tests
share it. The autouse fixture in conftest.py gives each test its own empty
home and sets DAIMON_CHECKPOINT_DIR to it, so a later test that drives a
surface through `drive_case` reads an empty store unless it points the
variable back at `world.bucket.parent`.

That is scar 0074's shape: nothing fails. A surface that lists buckets finds
none, never calls the function the test patched to raise, and an assertion
that looks at rc or text can pass for the wrong reason. The same applies to
`test_the_human_channel_exemption_is_closed_to_the_agent`.

When a new test needs the world, pin the checkpoint dir first, and make the
patched raise point something the surface is known to reach with data.
