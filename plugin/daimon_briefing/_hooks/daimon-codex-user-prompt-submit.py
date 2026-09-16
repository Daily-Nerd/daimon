#!/usr/bin/env python3
"""Codex UserPromptSubmit hook: the per-prompt recall injection (#1042).

Measured on Codex CLI 0.153.1 with daimon 0.46.0: `UserPromptSubmit` fires
once per user prompt, its stdin JSON carries `prompt`, `cwd`, `session_id`,
`turn_id` and `transcript_path`, and a nonce printed to plain stdout came
back verbatim in the model's next reply. So, unlike `SessionStart`, this
event needs no `hookSpecificOutput.additionalContext` envelope. Plain print
is enough, exactly like the Claude Code and Kimi prompt hooks.

Codex already gets its briefing from `daimon-codex-session-start.py`
(SessionStart), so unlike Kimi's UserPromptSubmit hook there is NO
first-prompt briefing branch here. This hook does exactly what the Claude
Code prompt hook (`daimon-prompt-recall.py`) does and nothing more:

1. Live request delivery (#756, opt-in via DAIMON_LIVE_DELIVERY, default
   off): an undecided ask addressed to this project reaches a session that
   was already running when it arrived.
2. Proactive 'you worked on this before' (#125): shells out to
   `daimon recall-inject` (the single source of truth for matching, noise
   gates, and cooldown) with the prompt on stdin.

Both ride ONE hook on purpose, same reasoning as the Claude Code hook: a
second registration would spawn a second interpreter on every prompt to
serve a feature that ships off.

Noise contract: this fires on EVERY prompt, so failures are SILENT (exit 0,
no output). A diagnostic line per prompt would be spam; the SessionStart
hook already surfaces install problems once per session.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 (missing/corrupt lib: silent no-op, see above)
    lib = None

TIMEOUT = 4  # seconds; the hooks.json registration budget is 5
# Delivery's slice of that budget, same split as the Claude Code hook: the
# two calls share one budget, and recall must not lose time it had before
# delivery existed. Whatever delivery leaves unspent stays with recall.
DELIVERY_TIMEOUT = 1.5


def _remaining(started: float) -> float:
    """Recall's timeout, minus whatever delivery actually spent. Never below
    one second: a squeezed budget should degrade recall, not disable it."""
    return max(1.0, TIMEOUT - (time.monotonic() - started))


def _deliver(cli, cwd: str, session: str) -> None:
    """Print any undecided asks this session has not been shown. Silent on
    every failure, like everything else on this path."""
    if not session:
        return  # the session id is half the delivery write-once key
    cmd = [cli, "request-inject", "--session", session]
    if cwd:
        cmd += ["--project", cwd]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=DELIVERY_TIMEOUT, env=lib.project_env(cwd),
        )
    except (subprocess.TimeoutExpired, OSError):
        return
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())


def main(argv=None) -> int:
    if lib is None or lib.disabled():
        return 0
    argv = sys.argv[1:] if argv is None else argv
    # #1036 parity: an argv flag, written by `daimon hooks install codex`
    # ONLY when it also registered the MCP entry in the same install, never
    # inferred here from host name or ancestry.
    mcp_tool = "--mcp-tool" in argv
    started = time.monotonic()
    data = lib.payload()
    prompt = str(data.get("prompt") or "")
    cwd = str(data.get("cwd") or "").strip()
    session = str(data.get("session_id") or "").strip()
    cli = lib.resolve_cli()
    if cli is None:
        return 0
    # #756, before the recall gate below and not subject to it.
    if (lib._config_get("DAIMON_LIVE_DELIVERY") or "").strip() in (
            "1", "true", "yes", "on"):
        _deliver(cli, cwd, session)
    # Slash commands are host directives, not work statements: never match.
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return 0
    cmd = [cli, "recall-inject"]
    if cwd:
        cmd += ["--project", cwd]
    if session:
        cmd += ["--session", session]
    recall_env = (lib.project_env(cwd, DAIMON_MCP_TOOL_AVAILABLE="1")
                 if mcp_tool else lib.project_env(cwd))
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=_remaining(started), env=recall_env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
