#!/usr/bin/env python3
"""The read-only MCP server's resolver wrapper (#1036), one file with five
consumers: `.claude-plugin/plugin.json`'s `mcpServers` entry (via
${CLAUDE_PLUGIN_ROOT}, same substitution hooks/hooks.json relies on for
every hook), the packaged Codex and Kimi Code installers (which copy this
file into each host's own hooks directory), the packaged Windsurf installer
(prints its path in a snippet the operator pastes into Cascade's config),
and the standalone Gemini lifecycle manager. Every one of them gets the
read-only `daimon_recall` (and sibling) tools without the operator running
`daimon mcp serve` or `claude mcp add` by hand.

Resolving the CLI is the one job this script has, and it does that the exact
way every daimon hook does: `_daimon_hook_lib.resolve_cli()` (`daimon` on
PATH, then the deprecated `daimon-briefing` alias, then the well-known
~/.local/bin fallbacks). A host's MCP config format cannot itself run that
fallback chain, and a path baked into that config would work only on the
machine that authored it. One resolver, reused everywhere, is the
alternative to five drifting from each other.

This process's image is replaced with the resolved CLI (`os.execv`) rather
than kept alive as a subprocess wrapper: the server speaks newline-delimited
JSON-RPC directly on stdio, and proxying every byte through a live parent
would buy nothing. Anything that keeps this script from reaching that exec
(disabled, unresolved, unexecutable) exits 0 silently — the same fail-open
posture every other daimon hook takes, because an MCP registration that
cannot start should not surface as a startup error to the host.
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
