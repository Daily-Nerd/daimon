---
id: 171
type: landmine
title: A host session-start hook that reads latest.json or slugs the cwd itself re-creates a second scope decision beside daimon brief
severity: high
confidence: 0.85
created: 2026-10-22
authors: ["Kibukx"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: hook/_daimon_hook_lib.py
  - path: hook/daimon-session-brief.py
  - path: hook/daimon-codex-session-start.py
  - path: hook/daimon-gemini-session-start.py
  - path: hook/daimon-kimi-user-prompt-submit.py
evidence:
  - note: Supersedes active scar 109: its expiry condition is met because the hooks now consume the CLI label. Archive 109 when promoting this replacement.
  - note: plugin/tests/test_hook_scope_owner.py scans every hook script for latest.json, lib.slug(, lib.checkpoint_dir(, lib.age_line( and a local fallback assignment
expires:
  condition: "the hooks are replaced by an in-process briefing call that takes a view.Scope"
  review_after: 2027-04-22
status: active
---

The four briefing hooks used to slug the payload cwd, check
`<ckpt>/<slug>/latest.json`, fall back to the global `latest.json`, exit early
when neither existed and label the age of the file THEY chose, while the body
came from `daimon brief`. The two could disagree: the header described another
project's checkpoint or a fallback the CLI never took, and the early exit hid
standing rulings and the handoff in a project with no checkpoint yet.

The scope owner is the CLI. `daimon brief --label` prints the one label line for
the checkpoint it rendered and `lib.run_brief` retries without the newest flag
for an older CLI. A new host hook calls `lib.run_brief` and prints the result;
it does not look at a pointer file, and a payload without a cwd is not a reason
to pick the global one.
