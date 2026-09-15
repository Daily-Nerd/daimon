"""#1036 parity: the read-only MCP server registration for Codex.

Codex's MCP config lives in ~/.codex/config.toml under [mcp_servers.<name>]
(confirmed live: `codex mcp add --help` and this machine's own config.toml
already carry [mcp_servers.obsidian] etc in exactly this shape), a separate
file from ~/.codex/hooks.json that codex_hooks.py already writes. That file
holds provider credentials and per-project trust state, so this never
re-serializes it: same posture as kimi_hooks.py, text in, text out, only our
own block touched.
"""

import json
from importlib import resources

from daimon_briefing import cli, codex_hooks

PKG = resources.files("daimon_briefing._hooks")


def _toml_home(tmp_path, seed=None):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    if seed is not None:
        (codex_dir / "config.toml").write_text(seed, encoding="utf-8")
    return tmp_path


def test_install_mcp_writes_the_wrapper_and_registers_the_table(tmp_path):
    home = _toml_home(tmp_path)
    lines = codex_hooks.install_mcp(PKG, home)
    wrapper = home / ".codex" / "hooks" / codex_hooks.MCP_SCRIPT
    assert wrapper.is_file()
    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.daimon]" in text
    assert 'command = "python3"' in text
    assert str(wrapper) in text
    assert any("registered" in ln for ln in lines)


def test_install_mcp_preserves_unrelated_toml_content_byte_for_byte(tmp_path):
    seed = (
        'model = "gpt-6"\n\n'
        '[mcp_servers.obsidian]\n'
        'url = "http://127.0.0.1:27123/mcp/"\n\n'
        '[projects."/repo/x"]\n'
        'trust_level = "trusted"\n'
    )
    home = _toml_home(tmp_path, seed)
    codex_hooks.install_mcp(PKG, home)
    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-6"' in text
    assert "[mcp_servers.obsidian]" in text
    assert 'url = "http://127.0.0.1:27123/mcp/"' in text
    assert '[projects."/repo/x"]' in text
    assert 'trust_level = "trusted"' in text


def test_install_mcp_is_idempotent(tmp_path):
    home = _toml_home(tmp_path)
    codex_hooks.install_mcp(PKG, home)
    first = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    lines = codex_hooks.install_mcp(PKG, home)
    second = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert first == second
    assert any("already" in ln for ln in lines)
    # No backup file: nothing changed, so nothing should be written at all.
    assert not list((home / ".codex").glob("config.toml.daimon-backup-*"))


def test_mcp_registered_reports_state(tmp_path):
    home = _toml_home(tmp_path)
    assert codex_hooks.mcp_registered(home) is False
    codex_hooks.install_mcp(PKG, home)
    assert codex_hooks.mcp_registered(home) is True


def test_remove_mcp_deletes_only_our_table(tmp_path):
    seed = '[mcp_servers.obsidian]\nurl = "http://127.0.0.1:27123/mcp/"\n'
    home = _toml_home(tmp_path, seed)
    codex_hooks.install_mcp(PKG, home)
    lines = codex_hooks.remove_mcp(home)
    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.daimon]" not in text
    assert "[mcp_servers.obsidian]" in text
    assert any("removed" in ln for ln in lines)
    assert codex_hooks.mcp_registered(home) is False


def test_remove_mcp_on_a_file_with_no_entry_is_a_clean_noop(tmp_path):
    home = _toml_home(tmp_path, "model = \"gpt-6\"\n")
    lines = codex_hooks.remove_mcp(home)
    assert any("no daimon" in ln for ln in lines)


def test_remove_mcp_without_a_config_file_is_not_an_error(tmp_path):
    home = _toml_home(tmp_path)  # .codex/ exists, config.toml does not
    lines = codex_hooks.remove_mcp(home)
    assert any("does not exist" in ln for ln in lines)


def test_install_mcp_appends_a_missing_trailing_newline_before_the_block(tmp_path):
    # A config.toml without a trailing newline (some editors never add one)
    # must not fuse its last line with the daimon marker comment.
    home = _toml_home(tmp_path, 'model = "gpt-6"')  # no trailing \n
    codex_hooks.install_mcp(PKG, home)
    text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-6"\n' in text
    assert "[mcp_servers.daimon]" in text


def test_mcp_registered_treats_an_undecodable_file_as_not_registered(tmp_path):
    home = _toml_home(tmp_path)
    (home / ".codex" / "config.toml").write_bytes(b"\xff\xfe not utf-8")
    assert codex_hooks.mcp_registered(home) is False


def test_cli_hooks_install_codex_also_registers_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert codex_hooks.mcp_registered(tmp_path) is True


def test_cli_hooks_remove_codex_removes_only_the_mcp_table(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["hooks", "install", "codex"]) == 0
    assert cli.main(["hooks", "remove", "codex"]) == 0
    assert codex_hooks.mcp_registered(tmp_path) is False
    # Hooks.json registrations are untouched: removal is MCP-only for codex.
    hooks_json = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert hooks_json["hooks"]["SessionStart"]
