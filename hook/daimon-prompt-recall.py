"""Claude Code UserPromptSubmit hook: the per-prompt injections.

Two of them, in this order:

1. Live request delivery (#756, opt-in via DAIMON_LIVE_DELIVERY, default
   off): an undecided ask addressed to this project reaches a session that
   was already running when it arrived, instead of waiting for that
   session's next SessionStart brief.
2. Proactive 'you worked on this before' (#125): shells out to
   `daimon recall-inject` (the single source of truth for matching, noise
   gates, and cooldown) with the prompt on stdin.

Both ride ONE hook rather than two entries on purpose. A second
UserPromptSubmit entry would spawn a second interpreter on every prompt for
every user (~36ms measured) to serve a feature that ships off; the flag is
read in-process instead, so a user who never enables delivery pays nothing.

Their noise gates are deliberately NOT shared. Recall skips slash commands
and empty prompts because a host directive is nobody asking about prior
work. An ask addressed to this project is owed regardless of what the user
typed, so delivery runs before that gate and independently of it.

Noise contract — this differs from the SessionStart brief hook on purpose:
this hook fires on EVERY prompt, so failures are SILENT (exit 0, no output).
A diagnostic line per prompt would be spam; the SessionStart hook already
surfaces install problems once per session. The only output this hook ever
produces is a real suggestion or a real ask.
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib: silent no-op (see above)
    lib = None

TIMEOUT = 4  # seconds; hooks.json budget is 5

# #1062: the exact name Claude Code's own tool list carries for the plugin's
# read-only MCP server — `mcp__plugin_<plugin>_<server>__<tool>`, built from
# the `mcpServers.daimon` key in .claude-plugin/plugin.json (server "daimon")
# and the plugin's own name ("daimon", same manifest). Only ever exported
# next to DAIMON_MCP_TOOL_AVAILABLE, so a host that never sets that flag
# never sees this name either.
MCP_TOOL_NAME = "mcp__plugin_daimon_daimon__daimon_recall"

# Delivery's slice of that budget. A local ledger read, so it is small on
# purpose: the two calls share one 5s hook budget, and recall must not lose
# time it had before delivery existed. Whatever delivery leaves unspent
# stays with recall (see `_remaining`), so the flag-off path is unchanged.
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
    # #1036 parity: an argv flag, not a shell env-var prefix on the manifest
    # command — a host that execs argv without a shell would treat `VAR=1`
    # as the program name and this hook would never start. The plugin's own
    # manifest is the one source of truth for whether it also registered the
    # MCP server (hooks/hooks.json passes this exactly where it does), never
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
    # #756, before the recall gate below and not subject to it. The flag read
    # is a file lookup, not a subprocess: off, this costs nothing.
    if (lib._config_get("DAIMON_LIVE_DELIVERY") or "").strip() in (
            "1", "true", "yes", "on"):
        _deliver(cli, cwd, session)
    # Slash commands are host directives, not work statements — never match.
    if not prompt.strip() or prompt.lstrip().startswith("/"):
        return 0
    cmd = [cli, "recall-inject"]
    if cwd:
        cmd += ["--project", cwd]
    if session:
        cmd += ["--session", session]
    recall_env = (lib.project_env(cwd, DAIMON_MCP_TOOL_AVAILABLE="1",
                                  DAIMON_MCP_TOOL_NAME=MCP_TOOL_NAME)
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
