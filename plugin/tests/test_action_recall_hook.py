"""#1031: the action-recall shim, `hook/daimon-action-recall.py <host>`.

This is a SEPARATE process from `daimon-pre-action.py` on purpose, and the
separation is the point of most of what follows. The pre-action hook is the
only daimon hook that can deny, and its stdout must be exactly one JSON
object; a recall fault inside that process would land in its outer handler
and drop the deny, byte-identical to an allow. A second interpreter keeps
that property structural rather than tested, so this shim shares nothing
with it: no import of `checks_host.py`, no import of the sibling script.

The other thing pinned here is the ladder. Every shipped row reads
`unsupported`, because neither host's `additionalContext` on PreToolUse has
been measured reaching the model. `unsupported` has to cost nothing at all,
not merely print nothing, or the ladder's bottom rung would be a spawned
interpreter per shell action for a channel nobody has seen work.
"""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hook" / "daimon-action-recall.py"

SESSION = "S-live"
COMMAND = "kubectl exec -it deploy/gateway -n prod -- sh"
LINE = 'daimon recall: prior work — question from S-old (3d ago): "x" [verbatim]'


@pytest.fixture
def mod():
    """The shim, imported by file location.

    By location and never by name: the filename is not an identifier, and the
    hosts run this file directly rather than importing it at all.
    """
    spec = importlib.util.spec_from_file_location("_daimon_action_recall",
                                                  HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


@pytest.fixture
def spawn(monkeypatch):
    """Record every subprocess the shim starts, and answer with `stdout`."""
    calls = []

    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Proc(fake.stdout)

    fake.stdout = LINE
    monkeypatch.setattr(subprocess, "run", fake)
    return calls


@pytest.fixture
def no_spawn(monkeypatch):
    """Turn any subprocess start into a failure, so "silent" can be told from
    "spawned an interpreter and then printed nothing"."""
    def boom(*a, **k):
        raise AssertionError("the shim spawned a subprocess it should not have")
    monkeypatch.setattr(subprocess, "run", boom)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DAIMON_ENV_FILE", raising=False)
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
    monkeypatch.delenv("DAIMON_ACTION_RECALL", raising=False)


def _payload(monkeypatch, session=SESSION, command=COMMAND, cwd="/repo/k8s"):
    data = {"session_id": session, "cwd": cwd,
            "tool_name": "Bash", "tool_input": {"command": command}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))


def _cli(mod, monkeypatch, path="/usr/local/bin/daimon"):
    monkeypatch.setattr(mod.lib, "resolve_cli", lambda: path)


def _run(mod, host="claude-code"):
    return mod.main([str(HOOK), host])


def _mode(mod, monkeypatch, host, mode):
    monkeypatch.setitem(mod.CAPS, host, mode)


# ---- the shipped ladder: every row unsupported ---------------------------


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_the_shipped_table_holds_both_hosts_at_unsupported(host, mod):
    # A documented channel is not a measured one. Until a probe watches
    # additionalContext reach the model, the honest row is unsupported.
    assert mod.CAPS[host] == "unsupported"


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_unsupported_costs_nothing_at_all(host, mod, monkeypatch, capsys,
                                          no_spawn):
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod, host) == 0
    assert capsys.readouterr().out == ""


# ---- record-only: measure, emit nothing ---------------------------------


def test_record_only_passes_the_flag_and_prints_nothing(mod, monkeypatch,
                                                        capsys, spawn):
    _mode(mod, monkeypatch, "claude-code", "record-only")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""
    cmd, kwargs = spawn[0]
    assert cmd[1] == "action-recall"
    assert "--record-only" in cmd
    assert cmd[cmd.index("--session") + 1] == SESSION
    assert cmd[cmd.index("--project") + 1] == "/repo/k8s"
    assert kwargs["input"] == COMMAND
    assert kwargs["timeout"] == 1.5


# ---- on: one JSON object, and never a decision --------------------------


def test_on_wraps_the_line_as_additional_context(mod, monkeypatch, capsys,
                                                 spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse",
                               "additionalContext": LINE}}
    # Stable bytes: sort_keys, so a host diffing hook output sees the change
    # it was told about and not a dict-ordering accident.
    assert out == json.dumps(json.loads(out), sort_keys=True)
    assert "--record-only" not in spawn[0][0]


def test_on_never_emits_a_permission_decision(mod, monkeypatch, capsys,
                                              spawn):
    # The whole reason this is a second process. This surface observes; the
    # pre-action hook decides. A decision key here would make a recall fault
    # able to allow or deny an action no check ever judged.
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    out = capsys.readouterr().out
    assert "permissionDecision" not in out


def test_on_with_nothing_to_say_says_nothing(mod, monkeypatch, capsys, spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    subprocess.run.stdout = "   \n"
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_a_non_zero_cli_exit_is_not_rendered(mod, monkeypatch, capsys):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Proc(LINE, returncode=3))
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


# ---- every silent path, and none of them spawn --------------------------


def test_an_unknown_host_argument_is_silent(mod, monkeypatch, capsys,
                                            no_spawn):
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod, "emacs") == 0
    assert capsys.readouterr().out == ""


def test_a_missing_host_argument_is_silent(mod, monkeypatch, capsys,
                                           no_spawn):
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert mod.main([str(HOOK)]) == 0
    assert capsys.readouterr().out == ""


def test_a_payload_without_a_session_id_is_silent(mod, monkeypatch, capsys,
                                                  no_spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch, session="")
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_a_payload_without_a_command_is_silent(mod, monkeypatch, capsys,
                                               no_spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch, command="   ")
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_an_unresolvable_cli_is_silent(mod, monkeypatch, capsys, no_spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch, path=None)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_the_flag_reads_the_env_file_not_just_the_process(
        mod, monkeypatch, tmp_path, capsys, no_spawn):
    # A GUI-launched host inherits none of the operator's shell exports, so a
    # process-env-only flag is a flag that host can never see.
    _mode(mod, monkeypatch, "claude-code", "on")
    env_file = tmp_path / ".daimon" / "env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("DAIMON_ACTION_RECALL=off\n", encoding="utf-8")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_the_flag_defaults_to_on_when_the_env_file_says_nothing(
        mod, monkeypatch, capsys, spawn):
    # The caps table is what holds this at unsupported, not the flag. An
    # absent flag must not double as a second off switch, or the ladder flip
    # would ship dead.
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out != ""


def test_the_kill_switch_silences_the_shim(mod, monkeypatch, capsys,
                                           no_spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_a_timeout_is_silent(mod, monkeypatch, capsys):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="daimon", timeout=1.5)
    monkeypatch.setattr(subprocess, "run", slow)
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_a_spawn_failure_is_silent(mod, monkeypatch, capsys):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert _run(mod) == 0
    assert capsys.readouterr().out == ""


def test_the_script_exits_zero_when_main_raises(mod, monkeypatch, tmp_path):
    # Run as the host runs it, in its own interpreter, with a payload that is
    # not JSON at all. Exit 2 is a documented unconditional block on both
    # hosts, so an escaping exception must never reach the exit code.
    proc = subprocess.run(
        [sys.executable, str(HOOK), "claude-code"], input="{not json",
        capture_output=True, text=True, timeout=60,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


# ---- the separation from the deny path ----------------------------------


def test_the_shim_shares_no_code_with_the_check_adapter():
    # Read off the import graph, not off the text: the docstring names the
    # check adapter to explain why it is NOT imported, and a substring search
    # would read that explanation as the violation it describes.
    import ast

    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"json", "subprocess", "sys", "pathlib",
                        "_daimon_hook_lib"}
    # And no file-location load of a sibling, which is how the check adapter
    # reaches its own core without an import statement.
    assert "spec_from_file_location" not in HOOK.read_text(encoding="utf-8")


# ---- registration: both installers have to be able to reach one file ----

REPO = Path(__file__).resolve().parents[2]
SCRIPT = "daimon-action-recall.py"


def _manifest(rel, name):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_plugin_registers_the_shim_behind_the_pre_action_hook():
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))["hooks"]
    groups = cfg["PreToolUse"]
    # Order is load-bearing: the deny path runs first, so an operator reading
    # the manifest sees the hook that can block before the one that observes.
    assert "daimon-pre-action.py" in groups[0]["hooks"][0]["command"]
    entry = groups[1]
    assert entry["matcher"] == "Bash"
    hook = entry["hooks"][0]
    assert hook["type"] == "command"
    # #1036: the plugin also declares the read-only MCP server in
    # .claude-plugin/plugin.json, so a claude-code session always has
    # `daimon_recall` listed. DAIMON_MCP_TOOL_AVAILABLE=1 tells the recall
    # hint to name that tool instead of the shell command — a shell env-var
    # prefix, because "command" (no "args") runs through a shell and a hook
    # entry has no separate "env" field.
    assert hook["command"] == (
        'DAIMON_MCP_TOOL_AVAILABLE=1 python3 "${CLAUDE_PLUGIN_ROOT}"/hook/'
        'daimon-action-recall.py claude-code')
    # Five against the shim's own 1.5s subprocess budget: the shim decides
    # well before the host gives up, and a recall is never worth a stall.
    assert hook["timeout"] == 5
    assert "statusMessage" not in hook


def test_the_plugin_flags_mcp_tool_availability_on_the_prompt_recall_hook():
    # #1036: same flag, same reasoning, on the surface that actually delivers
    # today (action-recall ships `unsupported` for claude-code — see CAPS).
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))["hooks"]
    hook = cfg["UserPromptSubmit"][0]["hooks"][0]
    assert hook["command"] == (
        'DAIMON_MCP_TOOL_AVAILABLE=1 python3 "${CLAUDE_PLUGIN_ROOT}"/hook/'
        'daimon-prompt-recall.py')


@pytest.mark.parametrize("rel", ["hook/codex-hooks.py",
                                 "plugin/daimon_briefing/codex_hooks.py"])
def test_both_codex_manifests_register_the_shim_with_its_host_argument(rel):
    hooks = _manifest(rel, f"_cx_{abs(hash(rel))}").HOOKS
    pre = [h for h in hooks if h["script"] == "daimon-codex-pre-action.py"]
    ours = [h for h in hooks if h["script"] == SCRIPT]
    assert len(ours) == 1 and len(pre) == 1
    assert hooks.index(pre[0]) < hooks.index(ours[0])
    entry = ours[0]["entry"]
    assert ours[0]["event"] == "PreToolUse"
    assert entry["matcher"] == "Bash|shell"
    cmd = entry["hooks"][0]
    assert cmd["command"] == f"python3 ~/.codex/hooks/{SCRIPT} codex"
    assert cmd["timeout"] == 5


def test_the_shim_is_in_the_sync_manifest():
    # A hand-copied hook outside SYNC_PAIRS drifts silently, and the packaged
    # copy is what `daimon hooks install codex` actually ships.
    sync = _manifest("scripts/sync_hooks.py", "_sync_for_action_recall")
    assert (f"hook/{SCRIPT}",
            f"plugin/daimon_briefing/_hooks/{SCRIPT}") in sync.SYNC_PAIRS


def test_the_codex_install_writes_the_shim(tmp_path, monkeypatch):
    from daimon_briefing import cli as cli_mod, codex_hooks

    assert SCRIPT in codex_hooks.FILES
    assert SCRIPT in cli_mod._HOOK_HOSTS["codex"]["files"]
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli_mod.main(["hooks", "install", "codex"]) == 0
    installed = tmp_path / ".codex" / "hooks" / SCRIPT
    assert installed.is_file()
    assert installed.read_bytes() == (REPO / "hook" / SCRIPT).read_bytes()
