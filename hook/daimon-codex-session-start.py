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
import time
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


def _age_line(latest: Path) -> str:
    # Host-specific: Codex's age line is mtime-only. It deliberately does NOT use
    # lib.age_line (which prefers the `created` stamp, #93) — kept as-is to hold
    # behavior byte-identical; adopting the created-stamp form is a follow-up.
    secs = max(0, time.time() - latest.stat().st_mtime)
    if secs < 3600:
        age = f"{int(secs // 60)}m"
    elif secs < 86400:
        age = f"{secs / 3600:.1f}h"
    else:
        age = f"{secs / 86400:.1f}d"
    try:
        session_id = json.loads(latest.read_text(encoding="utf-8")).get("session_id", "?")
    except Exception:
        session_id = "?"
    return f"(checkpoint: {session_id}, written {age} ago)"


def _emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


def _emit_briefing(cwd: str, cli) -> None:
    slug = lib.slug(cwd)
    ckpt_dir = lib.checkpoint_dir()
    latest = ckpt_dir / slug / "latest.json" if slug else None
    fallback = False
    if latest is None or not latest.exists():
        fallback = latest is not None
        latest = ckpt_dir / "latest.json"
    if not latest.exists():
        return

    if cli is None:
        _emit_context("daimon: checkpoint exists but `daimon` CLI was not found")
        return

    try:
        proc = subprocess.run(
            [cli, "brief"], capture_output=True, text=True, timeout=TIMEOUT,
            env=lib.project_env(cwd),
        )
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

    suffix = " (global fallback - checkpoint may be from another project)" if fallback else ""
    _emit_context(f"DAIMON BRIEFING {_age_line(latest)}{suffix}\n{text}")


def main() -> int:
    if lib is None:
        _emit_context("daimon: hook library missing (_daimon_hook_lib.py) - briefing skipped")
        return 0
    if lib.disabled():
        return 0

    cwd = str(lib.payload().get("cwd") or "").strip()
    cli = lib.resolve_cli()
    try:
        _emit_briefing(cwd, cli)
    except Exception as exc:  # fail-open, but say so
        _emit_context(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
    # Opportunistic self-heal runs regardless of whether a briefing emitted: a
    # failed serialize leaves NO checkpoint, exactly the case where heal matters.
    lib.spawn_heal(cli, cwd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _emit_context(f"daimon: hook error - briefing skipped ({type(exc).__name__}: {exc})")
        sys.exit(0)
