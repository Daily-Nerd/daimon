"""#1036 parity: the per-host capability table for the read-only MCP server
and the recall hint's tool form.

The rule the maintainer stated: a host-facing change ships for every
supported host in the same PR or not at all, with an explicit unsupported
row where a host cannot support it — never silence. This table IS that
explicit row set, in code, so a new host cannot be wired without one: the
test below ties its keys to the union of every place daimon already
registers a host (the packaged `hooks install` table plus the two hosts
that install another way, Claude Code's plugin manifest and Gemini's
standalone script).
"""

from daimon_briefing import cli, host_mcp_caps

_INSTALLER_HOSTS = set(cli._HOOK_HOSTS) | {"claude-code", "gemini"}


def test_the_table_covers_exactly_the_installers_host_set():
    assert set(host_mcp_caps.MCP_HOST_CAPS) == _INSTALLER_HOSTS


def test_every_row_carries_both_capabilities_from_the_known_vocabulary():
    for host, caps in host_mcp_caps.MCP_HOST_CAPS.items():
        assert set(caps) == {"mcp", "hint"}, host
        assert caps["mcp"] == "supported", host  # every host ships registration
        assert caps["hint"].startswith(("supported", "unsupported")), host


def test_only_the_two_hosts_with_a_prompt_time_recall_hook_support_the_hint():
    # claude-code (daimon-prompt-recall.py) and kimi
    # (daimon-kimi-user-prompt-submit.py) are the only hosts where a
    # recall-inject call exists at all today. Codex, Windsurf and Gemini have
    # no per-prompt recall hook (see their own module docstrings), so the
    # hint has nowhere to render regardless of MCP registration.
    supported = {h for h, c in host_mcp_caps.MCP_HOST_CAPS.items()
                if c["hint"] == "supported"}
    assert supported == {"claude-code", "kimi"}


def test_render_lines_names_every_host_and_both_columns():
    lines = host_mcp_caps.render_lines()
    text = "\n".join(lines)
    for host in _INSTALLER_HOSTS:
        assert host in text
    assert "mcp" in text.lower()
    assert "hint" in text.lower()
