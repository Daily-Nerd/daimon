---
id: 5
type: landmine
title: uv run daimon from repo root runs the stale GLOBAL tool, not the working tree
severity: medium
confidence: 0.95
created: 2026-07-02
authors: [claude, Kibukx]
anchors:
  - command: "uv run (?!--project\b)daimon\b"
violation: "uv run daimon"
evidence:
  - note: "2026-07-02: live verification of D-011 importance emission ran a 320s chunked serialize that silently produced a D-010 checkpoint — `uv run daimon serialize` from repo root had executed ~/.local/share/uv/tools/daimon-briefing (last `uv tool install` snapshot), not the branch code"
  - note: "2026-07-02, same day, second bite: bare `uv run pytest` resolved a leaked foreign VIRTUAL_ENV (fabcap/.venv), so the hook-subprocess e2e tests exercised the stale global tool via PATH — the new recall-inject hook test failed until rerun as `uv run --project plugin pytest`"
  - note: "firing-review 2026-09-24: dropped the README.md and docs/ path anchors — this scar is about RUNNING the stale global tool, not about editing prose that mentions it, and the two paths matched 1 + 27 = 28/948 tracked files for no act-proof signal. Narrowed the command anchor from a bare substring to `uv run (?!--project\\b)daimon\\b` so it fires only on the dangerous bare invocation and stops matching the already-fixed `uv run --project plugin daimon ...` form. Note: with no path/pattern anchor left, `violation:` can no longer arm — armed_candidates (match.py:387) only evaluates path/pattern anchors against edited files, never command anchors, so this scar's violation regex is now dead code; flagged for the maintainer, not fixed here since the instruction was anchor-only."
expires:
  condition: "pyproject.toml moves to the repo root, or the global `daimon` uv tool is installed --editable"
  review_after: 2027-01-01
status: active
---

The package's `pyproject.toml` lives in `plugin/`, not the repo root. `uv run daimon`
from the root therefore finds NO project to build, falls through to PATH, and executes
the globally installed uv tool at `~/.local/share/uv/tools/daimon-briefing` — a frozen
snapshot from the last `uv tool install`. Nothing errors, nothing warns; you get old
code wearing the new working tree's clothes. The failure is silent and expensive when
the command is LLM-bound (a wasted multi-minute serialize) and DANGEROUS when you are
verifying a code change: the "verification" exercises the snapshot, so a broken branch
can look green and a fixed branch can look broken.

The same resolution gap bites tests: a leaked `VIRTUAL_ENV` from another project makes
bare `uv run pytest` use THAT venv, and hook-subprocess tests (which exec `daimon` off
PATH) silently exercise the global tool.

To run working-tree code: `uv run --project plugin daimon ...` and
`uv run --project plugin pytest ...` (or run from inside `plugin/`). Verify which code
answered by checking a version-coupled marker in the output — e.g. `format_version` in
a written checkpoint — not just the exit code.
