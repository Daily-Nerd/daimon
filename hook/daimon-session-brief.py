#!/usr/bin/env python3
"""Claude Code SessionStart hook: inject the daimon briefing as session context.

Shells out to the installed `daimon brief` CLI (the single source of
truth for briefing rendering — same output as the hermes pre_llm_call path)
rather than re-rendering the checkpoint here, so the two hosts never drift.

Output contract (Claude Code SessionStart): anything printed to stdout is
injected as additional context for the session. Exit 0 always — a briefing
must never block a session (fail-open). Failures print a one-line diagnostic
instead of dying silently: silent non-injection is exactly the failure mode
this hook exists to dogfood.
"""

import subprocess
import sys
from pathlib import Path

# Shared helpers live in a same-dir sibling module (see _daimon_hook_lib.py).
# A stale/partial install may lack it: fail open with a one-line diagnostic
# rather than crash — silent non-injection is the failure mode this hook exists
# to dogfood, and a stack trace would be worse.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

TIMEOUT = 8  # seconds; hook budget is 10


def _run_brief(cli, cwd: str, auto: bool):
    argv = [cli, "brief", "--auto"] if auto else [cli, "brief"]
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT,
                          env=lib.project_env(cwd))


def _emit_briefing(cwd: str, cli) -> None:
    # No binary at all is the plugin-install onboarding state (#91): the plugin
    # ships the hooks, the CLI arrives separately. One actionable line, exit 0.
    if cli is None:
        print(
            "daimon: `daimon` CLI not found — briefing skipped. Install it: "
            "uv tool install 'git+https://github.com/Daily-Nerd/daimon#subdirectory=plugin'"
        )
        return

    # Per-project routing: prefer this project's latest (slugged from the
    # payload cwd); fall back to the global latest so pre-routing checkpoints
    # and fresh projects still brief — visibly labeled, since a global
    # checkpoint may belong to a DIFFERENT project.
    slug = lib.slug(cwd)
    ckpt_dir = lib.checkpoint_dir()
    latest = ckpt_dir / slug / "latest.json" if slug else None
    fallback = False
    if latest is None or not latest.exists():
        fallback = latest is not None  # project known, but no checkpoint of its own
        latest = ckpt_dir / "latest.json"
    if not latest.exists():
        return  # nothing to brief; stay quiet

    # The CLI must route the same way this hook did: hand it the project cwd.
    try:
        proc = _run_brief(cli, cwd, auto=True)
        if proc.returncode == 2:
            # argparse exit 2: a pre-flag `daimon` on PATH rejected --auto
            # (the plugin updates independently of the CLI). A briefing must
            # never die over instrumentation — retry plain.
            proc = _run_brief(cli, cwd, auto=False)
    except subprocess.TimeoutExpired:
        print(f"daimon: `daimon brief` timed out after {TIMEOUT}s — briefing skipped")
        return

    text = proc.stdout.strip()
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        print(f"daimon: brief failed (exit {proc.returncode}){': ' + err[0] if err else ''}")
        return
    if not text or text.startswith("No checkpoint yet"):
        return

    suffix = " (global fallback — checkpoint may be from another project)" if fallback else ""
    print(f"DAIMON BRIEFING {lib.age_line(latest)}{suffix}")
    print(text)


def main() -> int:
    if lib is None:
        print("daimon: hook library missing (_daimon_hook_lib.py) — briefing skipped")
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
        print(f"daimon: hook error — briefing skipped ({type(exc).__name__}: {exc})")
    # Opportunistic self-heal runs regardless of whether a briefing printed: a
    # failed serialize leaves NO checkpoint, exactly the case where heal matters.
    lib.spawn_heal(cli, cwd)
    # Opportunistic team sync (#113): detached, gated on a real sidecar remote
    # existing (cheap dir scan inside) — never blocks or delays the briefing.
    lib.spawn_team_sync(cli, cwd)
    # Orphan catch-up sweep (#185): recovers a `claude --resume` fork whose
    # SessionEnd never fired (app-quit kills hooks) by scanning this project's
    # transcript directory at the NEXT session's start instead. Runs LAST, after
    # the briefing is already on stdout — its own fail-open guarantee (see
    # lib.sweep_orphans) means it can never affect what the user already saw.
    lib.sweep_orphans(cli, cwd, session_id, transcript_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail-open, but say so
        print(f"daimon: hook error — briefing skipped ({type(exc).__name__}: {exc})")
        sys.exit(0)
