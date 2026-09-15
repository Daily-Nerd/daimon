"""#1036: .claude-plugin/plugin.json declares the read-only MCP server so a
plugin install lists `daimon_recall` (and its siblings) without the operator
running `daimon mcp serve` or `claude mcp add` by hand.

Inline in plugin.json rather than a separate .mcp.json at the plugin root:
the plugin already carries exactly one other manifest (hooks/hooks.json) for
its runtime surface, and a third top-level manifest file for one entry would
split one concern (what this plugin registers with the host) across three
files for no benefit — Claude Code's plugin schema accepts either shape,
substituting ${CLAUDE_PLUGIN_ROOT} in both the same way.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"


def _manifest() -> dict:
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))


def test_plugin_json_declares_the_read_only_mcp_server():
    servers = _manifest()["mcpServers"]
    assert set(servers) == {"daimon"}
    entry = servers["daimon"]
    # No path hard-coded into the manifest: it resolves the CLI exactly the
    # way every hook does, via the same resolve_cli() this test cross-checks
    # by asserting the manifest names the wrapper script, not a binary path.
    assert entry["command"] == "python3"
    assert entry["args"] == ['${CLAUDE_PLUGIN_ROOT}/hook/daimon-mcp-serve.py']


def test_no_other_top_level_mcp_manifest_exists():
    # The chosen shape is plugin.json's own mcpServers key; a stray
    # .mcp.json at the plugin root would be a second, silently-preferred
    # source of truth for the same one entry.
    assert not (REPO / ".mcp.json").exists()


def test_the_declared_wrapper_script_exists_and_is_executable():
    wrapper = REPO / "hook" / "daimon-mcp-serve.py"
    assert wrapper.exists()
    import os
    assert os.access(wrapper, os.X_OK)
