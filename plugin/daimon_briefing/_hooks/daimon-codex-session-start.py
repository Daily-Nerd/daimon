#!/usr/bin/env python3
"""Codex SessionStart hook: inject the latest daimon briefing as developer context.

Codex hook contract (official docs, verified 2026-06-12):
- command hooks receive JSON on stdin with common fields like session_id, cwd,
  transcript_path, hook_event_name, and model.
- SessionStart stdout can add extra developer context; JSON stdout can use
  hookSpecificOutput.additionalContext.

This script shells out to `daimon brief`, matching the Claude hook path
so the renderer stays single-source-of-truth.
"""

import json
import subprocess
import sys
from pathlib import Path

# Shared helpers live in a same-dir sibling module (see _daimon_hook_lib.py).
# A stale/partial install may lack it: fail open with a one-line diagnostic
# rather than crash.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

TIMEOUT = 8


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def _emit_briefing(cwd: str, cli) -> None:
    # No binary at all is the plugin-install onboarding state (#91): one
    # actionable line, the same one the Claude Code and Gemini hooks print.
    if cli is None:
        _emit_context(
            "daimon: `daimon` CLI not found - briefing skipped. Install it: "
            "uv tool install 'git+https://github.com/Daily-Nerd/daimon#subdirectory=plugin'"
        )
        return

    try:
        # #1089: same host tag `daimon-codex-session-end.py` already forwards
        # for capture (`project_env(cwd, "codex")`) — without it `daimon
        # brief` cannot tell which host is asking, and an `enforce` check
        # ruling never renders compact (a `request_policy` ruling is
        # unaffected either way; it renders compact regardless of host).
        proc = lib.run_brief(cli, cwd, "codex", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _emit_context(f"daimon: `daimon brief` timed out after {TIMEOUT}s")
        return

    text = proc.stdout.strip()
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        msg = f"daimon: brief failed (exit {proc.returncode})"
        _emit_context(msg + (f": {err[0]}" if err else ""))
        return
    if not text or text.startswith("No checkpoint yet"):
        return

    _emit_context(text)


def main() -> int:
    if lib is None:
        _emit_context("daimon: hook library missing (_daimon_hook_lib.py) - briefing skipped")
        return 0
    if lib.disabled():
        return 0

    data = lib.payload()
    cwd = str(data.get("cwd") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    transcript_path = str(data.get("transcript_path") or "").strip()
    cli = lib.resolve_cli()
    try:
        _emit_briefing(cwd, cli)
    except Exception as exc:  # fail-open, but say so
        _emit_context(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
    # Opportunistic self-heal runs regardless of whether a briefing emitted: a
    # failed serialize leaves NO checkpoint, exactly the case where heal matters.
    lib.spawn_heal(cli, cwd)
    # Orphan catch-up sweep (#188), porting Claude Code's #185 mechanism as-is:
    # Codex's Stop hook throttles serialize per session, so a session whose
    # final activity lands inside the throttle window and never resumes is
    # never captured again. Scanning this session's transcript-sibling
    # directory at the NEXT session's start recovers it. Runs LAST, after the
    # briefing is already emitted — lib.sweep_orphans' own fail-open guarantee
    # means it can never affect what Codex already saw.
    lib.sweep_orphans(cli, cwd, session_id, transcript_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _emit_context(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
        sys.exit(0)
