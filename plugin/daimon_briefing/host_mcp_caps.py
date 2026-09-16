"""#1036 parity: per-host capability table for the read-only MCP server and
the recall hint's tool form.

Host parity rule: a host-facing change ships for every supported host in the
same PR or not at all, with an explicit unsupported row where a host cannot
support it. This module IS that explicit row set — every host daimon
registers with, in one place, so a new host cannot be wired without adding a
row here (tests/test_mcp_host_caps.py ties the key set to the union of the
packaged `hooks install` table and the two hosts that register another way).

Two independent columns, because they answer different questions:
  - "mcp": does `daimon hooks install <host>` (or the plugin manifest, or the
    standalone gemini-hooks.py) register the read-only MCP server for this
    host? Every shipped host does, as of #1036.
  - "hint": does the per-prompt recall hint have anywhere to render on this
    host, i.e. does a prompt-time recall-inject call exist at all?
    claude-code (daimon-prompt-recall.py), kimi
    (daimon-kimi-user-prompt-submit.py) and, since #1042, codex
    (daimon-codex-user-prompt-submit.py) have one. Windsurf and Gemini are
    briefing-only or action-only hosts; registering the MCP server there is
    still worthwhile (a host's own agent can call the tool directly), but
    there is no hint text to swap forms on.
"""

MCP_HOST_CAPS: dict[str, dict[str, str]] = {
    "claude-code": {"mcp": "supported", "hint": "supported"},
    "kimi": {"mcp": "supported", "hint": "supported"},
    "codex": {"mcp": "supported", "hint": "supported"},
    "windsurf": {"mcp": "supported",
                "hint": "unsupported (no prompt-time recall hook)"},
    "gemini": {"mcp": "supported",
              "hint": "unsupported (no prompt-time recall hook)"},
}


def render_lines() -> list[str]:
    """Plain-text rows, host-sorted: `host  mcp=...  hint=...`. Used for
    `daimon status`-style output and to keep the docs table honest by hand
    (see website/docs/reference/mcp.md)."""
    lines = []
    for host in sorted(MCP_HOST_CAPS):
        caps = MCP_HOST_CAPS[host]
        lines.append(f"{host:12} mcp={caps['mcp']:11} hint={caps['hint']}")
    return lines
