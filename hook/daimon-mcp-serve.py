#!/usr/bin/env python3
"""Claude Code plugin MCP server entry point (#1036).

.claude-plugin/plugin.json declares a `daimon` entry under `mcpServers`
pointing at this script (via ${CLAUDE_PLUGIN_ROOT}, same substitution
hooks/hooks.json already relies on for every other hook), so a plugin
install gets the read-only `daimon_recall` (and sibling) tools without the
operator running `daimon mcp serve` or `claude mcp add` by hand.

Resolving the CLI is the one job this script has, and it does that the exact
way every other Claude Code hook does: `_daimon_hook_lib.resolve_cli()`
(`daimon` on PATH, then the deprecated `daimon-briefing` alias, then the
well-known ~/.local/bin fallbacks). A manifest cannot itself run that
fallback chain — JSON has no `or` — and a path hard-coded into the manifest
would work only on the machine that authored it. One resolver, reused, is
the alternative to a second one drifting from the first.

This process's image is replaced with the resolved CLI (`os.execv`) rather
than kept alive as a subprocess wrapper: the server speaks newline-delimited
JSON-RPC directly on stdio, and proxying every byte through a live parent
would buy nothing. Anything that keeps this script from reaching that exec
(disabled, unresolved, unexecutable) exits 0 silently — the same fail-open
posture every other daimon hook takes, because a plugin's mcpServers entry
that cannot start should not surface as a startup error to the host.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib: silent no-op
    lib = None


def main() -> int:
    if lib is None or lib.disabled():
        return 0
    cli = lib.resolve_cli()
    if cli is None:
        return 0
    try:
        os.execv(cli, [cli, "mcp", "serve"])
    except OSError:
        return 0
    return 0  # unreachable on a successful execv; kept for callers/tests


if __name__ == "__main__":
    sys.exit(main())
