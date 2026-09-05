---
id: 0
type: deadend
title: Serialize outcomes reach a container host through inherited stdout, never through stderr; stderr already means crash here
severity: medium
confidence: 0.85
created: 2026-09-05
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/_hooks/_daimon_hook_lib.py
  - path: plugin/daimon_briefing/cli/__init__.py
  - pattern: "stderr\\s*=\\s*(crashf|subprocess\\.DEVNULL)"
evidence:
  - pr: 194
  - note: "2026-09-05: a downstream consumer running daimon in a long-lived container asked for an opt-in stderr mirror of serialize outcomes, because a file under ~/.daimon/logs is invisible to a container runtime. Implemented literally, it lands in serialize-crash.log, another file on the same volume, and ships nothing."
expires:
  condition: "spawn_serialize stops routing child stderr to crash_log_path(), or #194's separation of diagnostics from crash output is retired"
  review_after: 2027-03-05
status: candidate
---

Making a serialize outcome visible to a container host looks like a stderr job, and
that is the shape the field request will arrive in. It is wrong twice.

`spawn_serialize` routes the detached child's stderr to `crash_log_path()`, a separate
file, on purpose: #194 moved serializer and llm diagnostics off stderr because stderr
lands in `serialize-crash.log` and misreads as a crash. So stderr already carries a
meaning, and mirroring result lines onto it both breaks that separation and still
writes to a file the runtime cannot collect.

Use stdout. It carries the result lines already (`_run_serialize` prints every `msg`
next to its `_append_serialize_log` call), and it is set to `DEVNULL` at the spawn
only to avoid double-logging into `serialize.log`, which an inherited descriptor does
not do. Gate it on an env var and leave it off by default: on a terminal host an
inherited descriptor prints capture results into the user's shell minutes after the
session ended. `project_env` copies the whole environment with no whitelist, so a
host-level variable already reaches the child.
