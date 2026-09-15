"""#1036: hook/daimon-mcp-serve.py, the command .claude-plugin/plugin.json's
mcpServers entry actually runs.

The manifest cannot itself run a shell fallback chain (`daimon`, then
`daimon-briefing`, then a well-known path) the way every hook script's
`_daimon_hook_lib.resolve_cli()` does, and a path hard-coded into the
manifest would work only on the machine that authored it. This script is the
manifest's `command`: it resolves the CLI exactly the way the hooks do, then
execs it — no path in the manifest, and one resolver, not two.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "hook" / "daimon-mcp-serve.py"
VENV_BIN = Path(sys.executable).parent  # holds the `daimon` console script


@pytest.fixture
def mod():
    """The wrapper, imported by file location — same technique as the other
    hook-script tests, and for the same reason: hosts run this file
    directly, never import it by name."""
    spec = importlib.util.spec_from_file_location("_daimon_mcp_serve", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_disabled_kill_switch_exits_clean_without_resolving(mod, monkeypatch):
    monkeypatch.setattr(mod.lib, "disabled", lambda: True)
    calls = []
    monkeypatch.setattr(mod.lib, "resolve_cli", lambda: calls.append(1) or "x")
    monkeypatch.setattr(mod.os, "execv", lambda *a: pytest.fail("must not exec"))
    assert mod.main() == 0
    assert calls == []  # never even resolved: the kill switch is checked first


def test_cli_not_found_exits_clean(mod, monkeypatch):
    monkeypatch.setattr(mod.lib, "disabled", lambda: False)
    monkeypatch.setattr(mod.lib, "resolve_cli", lambda: None)
    monkeypatch.setattr(mod.os, "execv", lambda *a: pytest.fail("must not exec"))
    assert mod.main() == 0


def test_resolved_cli_is_exec_d_with_mcp_serve_argv(mod, monkeypatch):
    monkeypatch.setattr(mod.lib, "disabled", lambda: False)
    monkeypatch.setattr(mod.lib, "resolve_cli", lambda: "/opt/bin/daimon")
    calls = []
    monkeypatch.setattr(mod.os, "execv", lambda path, argv: calls.append((path, argv)))
    assert mod.main() == 0
    assert calls == [("/opt/bin/daimon", ["/opt/bin/daimon", "mcp", "serve"])]


def test_execv_oserror_is_caught_and_exits_clean(mod, monkeypatch):
    # A resolved path that turns out unexecutable (permissions, deleted
    # between resolve and exec) must not crash the host's plugin loader.
    monkeypatch.setattr(mod.lib, "disabled", lambda: False)
    monkeypatch.setattr(mod.lib, "resolve_cli", lambda: "/opt/bin/daimon")

    def _raise(*a):
        raise OSError("no such file")
    monkeypatch.setattr(mod.os, "execv", _raise)
    assert mod.main() == 0


def test_missing_lib_import_is_a_silent_no_op(mod, monkeypatch):
    monkeypatch.setattr(mod, "lib", None)
    assert mod.main() == 0


def test_end_to_end_the_wrapper_serves_the_same_tools_list_as_the_real_cli(
        tmp_path):
    # #1036: proves the execv handoff does not disturb stdio — a real
    # subprocess, real `daimon` on PATH, one JSON-RPC line in, one line out.
    env = {
        **os.environ,
        "PATH": f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "DAIMON_CHECKPOINT_DIR": str(tmp_path / "checkpoints"),
        "DAIMON_LOG_DIR": str(tmp_path / "logs"),
    }
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=request, capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    tools = json.loads(lines[0])["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"daimon_recall", "daimon_brief", "daimon_projects",
                     "daimon_status", "requests_inbox"}
    for t in tools:
        assert t["annotations"]["readOnlyHint"] is True
