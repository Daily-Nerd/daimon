#!/usr/bin/env python3
"""Gemini CLI SessionStart hook: inject the latest daimon briefing as context.

Gemini hook contract (docs/hooks/reference.md, verified 2026-07-01):
- stdin carries JSON with session_id, transcript_path, cwd, hook_event_name,
  timestamp, and SessionStart adds `source` ("startup" | "resume" | "clear").
- stdout must be PURE JSON ("Silence is Mandatory") — plain text breaks the
  host's parsing, so unlike the Claude Code hook NOTHING may be print()ed raw.
- Context injection rides {"hookSpecificOutput": {"additionalContext": ...}};
  operator-facing diagnostics ride {"systemMessage": ...} instead.
- SessionStart is advisory-only: startup is never blocked, exit 0 always.

This script shells out to `daimon brief`, matching the Claude Code and Codex
hook paths so the renderer stays single-source-of-truth.
"""

import json
import subprocess
import sys
from pathlib import Path

# Shared helpers live in a same-dir sibling module (see _daimon_hook_lib.py).
# A stale/partial install may lack it: fail open with a systemMessage one-liner
# (never plain stdout — Gemini requires pure JSON) rather than crash.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

TIMEOUT = 8  # seconds; the installed Gemini hook budget is 10000 ms


def _emit_context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"additionalContext": text}}))


def _emit_message(text: str) -> None:
    """Operator-facing one-liner. Gemini shows systemMessage in the terminal —
    the right channel for diagnostics that would be plain stdout on Claude Code."""
    print(json.dumps({"systemMessage": text}))


def _emit_briefing(cwd: str, cli) -> None:
    # No binary at all is the plugin-install onboarding state (#91): the plugin
    # ships the hooks, the CLI arrives separately. One actionable line, exit 0.
    if cli is None:
        _emit_message(
            "daimon: `daimon` CLI not found — briefing skipped. Install it: "
            "uv tool install 'git+https://github.com/Daily-Nerd/daimon#subdirectory=plugin'"
        )
        return

    # The CLI decides which checkpoint is read and labels it (#1223); this
    # hook hands it the project cwd and prints what comes back.
    try:
        proc = lib.run_brief(cli, cwd, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _emit_message(f"daimon: `daimon brief` timed out after {TIMEOUT}s — briefing skipped")
        return

    text = proc.stdout.strip()
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        msg = f"daimon: brief failed (exit {proc.returncode})"
        _emit_message(msg + (f": {err[0]}" if err else ""))
        return
    if not text or text.startswith("No checkpoint yet"):
        return

    _emit_context(text)


def main() -> int:
    if lib is None:
        _emit_message("daimon: hook library missing (_daimon_hook_lib.py) - briefing skipped")
        return 0
    if lib.disabled():
        return 0

    cwd = str(lib.payload().get("cwd") or "").strip()
    cli = lib.resolve_cli()
    try:
        _emit_briefing(cwd, cli)
    except Exception as exc:  # fail-open, but say so
        _emit_message(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
    # Opportunistic self-heal runs regardless of whether a briefing emitted: a
    # failed serialize leaves NO checkpoint, exactly the case where heal matters.
    lib.spawn_heal(cli, cwd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _emit_message(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
        sys.exit(0)
