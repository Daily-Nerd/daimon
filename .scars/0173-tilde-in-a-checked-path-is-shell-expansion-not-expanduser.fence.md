---
id: 173
type: fence
title: A tilde in a checked path argument is reported as shell-expansion, never resolved with expanduser
severity: low
confidence: 0.8
created: 2026-11-06
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/checks_runtime.py
evidence:
  - commit: 91178f4
expires:
  condition: "the tokenizer keeps quote information per token"
  review_after: 2027-05-06
status: active
---

`_needs_shell_expansion` flags a leading `~` (and `$`, backtick, `*`, `?`, `[`)
instead of calling `expanduser`. It looks like a missing convenience, but shlex
in posix mode drops quotes, so `"~/x"` (a literal directory named `~` in the
shell) and `~/x` arrive as the same token. Converting would read the wrong file
for the quoted form. A literal filename containing `*` or `$` is flagged too,
and that is accepted: it stays unresolved and the message says to pass a literal
path. Do not add expanduser or glob expansion without per-token quote tracking.
Issue #1282.
