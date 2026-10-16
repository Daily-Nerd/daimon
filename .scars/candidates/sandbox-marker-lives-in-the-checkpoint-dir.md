---
id: 0
type: fence
title: The sandbox marker sits in the checkpoint dir on purpose, and the seeders compare it against the dir config resolves NOW
severity: medium
confidence: 0.8
created: 2026-10-16
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/testing.py
evidence:
  - note: "#1179 PR 5, design D21 (marker under the current checkpoint dir)"
expires:
  condition: "seed_checkpoint and seed_ruling stop writing a ratified ruling, or the sandbox gains a second guard"
  review_after: 2027-04-16
status: candidate
---

`testing.sandbox` writes a random token to `<checkpoints>/.sandbox`, and
`seed_checkpoint` / `seed_ruling` read it back through `config.checkpoint_dir()`
at call time. A token kept only in the ContextVar, or a marker at the sandbox
root, would let `checkpoint_dir_override(elsewhere)` inside the sandbox send a
ratified ruling into a real store while the guard still passed. Reading the
marker from the dir the config resolves now is what makes a re-pointed dir fail.

The file is inert in the store: every scan of the checkpoint dir keeps only
`*.json`, `*.tmp` and subdirectories (store.py `_session_files`,
`_plaintext_surfaces`, `_reap_stale_tmps`). Do not rename it to `.json`, and do
not move it to the root.

A second oddity: `config.settings_override` answers a DAIMON_* name from the
mapping alone, so `heal.child_env(settings=...)` must drop every other DAIMON_*
name and null the env file for the child. Changing one without the other makes
a sandboxed heal child read the developer's real environment.
