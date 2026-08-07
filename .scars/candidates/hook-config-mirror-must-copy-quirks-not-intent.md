---
id: 0
type: landmine
title: A hook mirroring a config accessor must copy its QUIRKS (no-strip, env-file fallback), not its intent — divergence silently splits a writer from its deleter
severity: high
confidence: 0.95
created: 2026-08-06
authors: ["claude-code"]
anchors:
  - path: hook/
  - path: plugin/daimon_briefing/config.py
evidence:
  - note: "2026-08-06, #605. crash_log_path() in hook/_daimon_hook_lib.py was written as os.environ.get(DAIMON_LOG_DIR, '').strip(). Adversarial review found both halves wrong: config._get falls back to ~/.daimon/env, and config.log_dir() never strips. With DAIMON_LOG_DIR set only in the env file, the serialize child wrote tracebacks to ~/.daimon/logs while `daimon forget` purged the configured directory and printed a clean zero — permanent plaintext residue under a surface registry entry that declared the file reachable by forget."
  - note: "2026-08-06, prior bite of the same class: #607, where hook/daimon-windsurf-hooks.py hardcoded ~/.daimon/windsurf while the package honored DAIMON_WINDSURF_DIR, so purge/reap/audit all reported cleanly on an empty directory while the adapter filled another one."
expires:
  condition: "hooks can import daimon_briefing.config directly, or both sides call one shared hook-safe resolver instead of two copies"
  review_after: 2027-02-06
violation: "os\.environ\.get\(.DAIMON_LOG_DIR"
status: candidate
---

`daimon forget` deletes `logs/serialize-crash.log` through `config.log_dir()`,
so a hook resolving that path must reproduce config's accessor exactly —
including the parts that look like bugs. Two natural simplifications each split
the writer from the deleter SILENTLY, because a one-file purge reporting
`purged 0` is indistinguishable from an empty log dir:

- **Process env only.** `config._get` falls back to `~/.daimon/env` (scar #36).
  That file is not exotic: it exists because a GUI-launched host inherits no
  shell profile, so shell exports never reach it. Set there and nowhere else,
  the child wrote to `~/.daimon/logs` while forget purged the configured
  directory and reported clean.
- **`.strip()` on the value.** `config.log_dir()` does not strip, so `"   "` is
  a RELATIVE directory named three spaces and `"  /x  "` keeps its spaces. A
  stripping writer resolves both somewhere the purge never looks.

Hooks cannot import the package, so the duplicated parser is unavoidable. What
keeps it honest is a behavioral-equality probe table asserting identity with the
config function — over process-env values AND env-file line forms (quoting,
`export`, whitespace, comments, empty value) — never against hand-written
literals a test author has to get right twice. See
`plugin/tests/test_crash_log_privacy.py`, and the same idiom at
`test_claude_hooks.py` for `hung_after_seconds`. Deliberate exception: values
nothing deletes (serialize.log's `LOG_DIR`, `DAIMON_HUNG_AFTER`) stay
process-env-only — no deleter, so nothing has to agree.
