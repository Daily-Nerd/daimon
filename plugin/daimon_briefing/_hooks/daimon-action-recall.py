#!/usr/bin/env python3
"""PreToolUse hook: recall keyed on the shell command (#1031).

    python3 daimon-action-recall.py <host>

Per-prompt recall keys on the prompt text. When the prompt carries no words
about the action the agent is about to take, the action gets no memory in
front of it: a prompt asking for "recommended improvements" returned nothing
about deploys, and the agent then wrote to a pod while a belief saying that
ArgoCD selfHeal reverts manual edits sat in the store the whole time. The
command string alone carries enough signal, so this fires on the command.

WHY THIS IS ITS OWN PROCESS, and not a few lines inside the pre-action hook:
`daimon-pre-action.py` is the only daimon hook that can FAIL a host action.
Its stdout must be exactly one JSON object, and a recall fault raised inside
it between the decision and the write would land in its outer handler, which
exits 0 with empty stdout. That is byte-identical to a clean allow, so a
denial a human ratified would silently not happen. A second interpreter makes
that impossible rather than merely tested. The price is one extra process per
shell action, and it is worth it. Nothing here imports `checks_host.py`,
`checks_runtime.py` or the sibling script, and nothing here ever emits a
`permissionDecision` key.

Output contract: exit 0 always, stderr silent, stdout empty or exactly one
JSON object carrying `additionalContext`.

Per-host ladder, in CAPS below. `unsupported` is where every row ships:
both hosts DOCUMENT an additionalContext channel on PreToolUse and neither
delivery has been measured reaching the model, and a hook that claims to be
delivering into a channel nobody has watched work is the one failure mode
this ladder exists to prevent (the same argument as the check adapter's
per-host mode caps). A later commit flips a row citing its probe.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib: silent no-op
    lib = None

# The shim's own budget, well inside the 5s registered with both hosts. A
# recall is never worth a stall in front of an action.
TIMEOUT = 1.5

# host -> what this host can actually deliver.
#   unsupported = do nothing, and spawn nothing
#   record-only = run the query so the ledger row exists, emit nothing
#   on          = run the query and emit the line
CAPS = {
    "claude-code": "unsupported",
    "codex": "unsupported",
}

# Where both hosts carry the shell command. Replicated from
# checks_host.PROFILES rather than imported — see the module docstring; two
# lines of duplication are the price of not sharing a process with the deny
# path. Windsurf carries it elsewhere and has no row here.
COMMAND_PATH = ("tool_input", "command")


def _command(data: dict) -> str:
    node: object = data
    for key in COMMAND_PATH:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return str(node or "")


def main(argv) -> int:
    if lib is None or lib.disabled():
        return 0
    # argv[0] is the script path; the host is the first token that is not a
    # flag, so `--mcp-tool` can precede or follow it without changing which
    # positional wins.
    rest = argv[1:]
    mcp_tool = "--mcp-tool" in rest
    host = next((a for a in rest if not a.startswith("--")), "")
    # Before stdin, before the flag, before any spawn: an unsupported or
    # unknown host costs one interpreter start and nothing else.
    if CAPS.get(host, "unsupported") == "unsupported":
        return 0
    mode = CAPS[host]
    # A file lookup, not a subprocess, and deliberately not the process env
    # alone: a GUI-launched host inherits none of the operator's shell
    # exports, so an env-only flag is one that host can never see.
    # Same four words and the same no-lower, no-extra-tolerance shape the
    # live-delivery gate uses: a mirrored accessor has to copy the sibling's
    # QUIRKS, not its intent, or an operator learns one spelling and finds it
    # silently ignored on the other surface (scar 0043).
    if (lib._config_get("DAIMON_ACTION_RECALL") or "").strip() in (
            "0", "false", "no", "off"):
        return 0
    data = lib.payload()
    session = str(data.get("session_id") or "").strip()
    command = _command(data)
    # The session id keys the cooldown, and the command IS the query. Without
    # either there is nothing to ask and nothing to suppress with.
    if not session or not command.strip():
        return 0
    cli = lib.resolve_cli()
    if cli is None:
        return 0
    cwd = str(data.get("cwd") or "").strip()
    cmd = [cli, "action-recall", "--session", session]
    if cwd:
        cmd += ["--project", cwd]
    if mode == "record-only":
        cmd.append("--record-only")
    # #1036 parity: same accessor recall-inject uses, same reasoning — an
    # argv flag on THIS shim exports the env var into the CLI's own env
    # rather than relying on a shell-interpreted prefix in the manifest.
    env = (lib.project_env(cwd, DAIMON_MCP_TOOL_AVAILABLE="1") if mcp_tool
          else lib.project_env(cwd))
    try:
        proc = subprocess.run(
            cmd, input=command, capture_output=True, text=True,
            timeout=TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if mode == "record-only":
        return 0
    if proc.returncode == 0 and proc.stdout.strip():
        # additionalContext and nothing else. No permissionDecision key on
        # this surface, ever: this hook observes, the pre-action hook decides.
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": proc.stdout.strip(),
            },
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:  # noqa: BLE001 — fail open, and silently: stdout is
        # parsed as JSON by the host, so a diagnostic printed there is a
        # decision it cannot read.
        sys.exit(0)
