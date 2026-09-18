"""#1031: the action-recall shim, `hook/daimon-action-recall.py <host>`.

This is a SEPARATE process from `daimon-pre-action.py` on purpose, and the
separation is the point of most of what follows. The pre-action hook is the
only daimon hook that can deny, and its stdout must be exactly one JSON
object; a recall fault inside that process would land in its outer handler
and drop the deny, byte-identical to an allow. A second interpreter keeps
that property structural rather than tested, so this shim shares nothing
with it: no import of `checks_host.py`, no import of the sibling script.

The other thing pinned here is the ladder. `unsupported` is where every row
ships until its host's `additionalContext` on PreToolUse has been measured
reaching the model, and it has to cost nothing at all, not merely print
nothing, or the ladder's bottom rung would be a spawned interpreter per shell
action for a channel nobody has seen work. Claude Code 2.1.272 cleared that
measurement (#1046): a `claude -p` run accepted the field and ran the
command, but the stream showed `tool_use`, then `tool_result`, then assistant
text, with no model turn in between. The context lands WITH the tool
result, so it can only inform the NEXT action, never the one it fired on.
That is why `claude-code` moved to `record-only` and not straight to `on`:
the query runs and the ledger row lands, nothing prints, and the `on` rung
waits on those rows showing the line would have been used. `codex` stays
`unsupported`, its own probe measured a different, per-prompt channel.
"""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from daimon_briefing import cli as real_cli
from daimon_briefing import store

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


# ---- the shipped ladder: claude-code record-only, codex still unsupported -


def test_the_shipped_table_moves_claude_code_to_record_only(mod):
    # #1046: measured on Claude Code 2.1.272. additionalContext on
    # PreToolUse is accepted, but it lands with the tool result, informing
    # only the NEXT action. record-only runs the query so the ledger carries
    # that data without claiming to have informed anything.
    assert mod.CAPS["claude-code"] == "record-only"


def test_codex_stays_unsupported(mod):
    # Codex's own probe measured a different, per-prompt channel and
    # deny-wins with both PreToolUse hooks, not additionalContext delivery.
    # That row waits on its own receipt.
    assert mod.CAPS["codex"] == "unsupported"


def test_codex_still_costs_nothing_at_all(mod, monkeypatch, capsys, no_spawn):
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod, "codex") == 0
    assert capsys.readouterr().out == ""


def test_an_unsupported_host_costs_nothing_at_all(mod, monkeypatch, capsys,
                                                  no_spawn):
    # Same assertion, held generically via _mode so unsupported's own
    # contract stays pinned independent of which row ships at it today.
    _mode(mod, monkeypatch, "claude-code", "unsupported")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
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


# ---- record-only, end to end: a real ledger row, no mock in the middle ----
#
# Every test above fakes subprocess.run to answer with a canned line, which
# proves the shim's OWN argv/env/mode plumbing but cannot tell a real
# record-only run from a real `on` run: both are "some subprocess ran, and
# X was in `cmd`". The tests below route the shim's subprocess call into the
# actual `daimon_briefing.cli.main`, in-process, so what gets asserted is the
# thing #1046 actually shipped: a delivery row lands on disk and stdout stays
# empty, for the host now carrying `record-only` in `CAPS`.


@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse _isolated_home fixture points HOME here; DAIMON_LOG_DIR
    # (set by the module-level conftest fixture, sharing this same tmp_path)
    # resolves under it the same way.
    return tmp_path / ".daimon" / "logs"


@pytest.fixture
def real_subprocess(monkeypatch):
    """Answer the shim's OWN subprocess.run call by running the real CLI
    in-process, instead of a canned line, and forward every OTHER call
    (`config.py`'s own `git rev-parse` among them; `subprocess` is one
    module-level object, so a blanket patch here would hit that too) to the
    real `subprocess.run` untouched.

    `cmd[0]` is whatever `_cli()` set `resolve_cli` to return: irrelevant
    here, since the real CLI never spawns a second process either. Only
    `cmd[1:]` (the argv `daimon` itself would see) and `input` (stdin) cross
    over."""
    real_run = subprocess.run

    def fake(cmd, input=None, capture_output=True, text=True, timeout=None,
             env=None, **kwargs):
        if not (isinstance(cmd, list) and len(cmd) > 1
                and cmd[1] == "action-recall"):
            return real_run(cmd, input=input, capture_output=capture_output,
                            text=text, timeout=timeout, env=env, **kwargs)
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(input or "")
        sys.stdout = io.StringIO()
        try:
            rc = real_cli.main(cmd[1:])
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout
        return _Proc(out, rc)
    monkeypatch.setattr(subprocess, "run", fake)


def _checkpoint_body(session, text, created):
    return {
        "session_id": session,
        "created": created,
        "working_context": {
            "active_topic": {"text": "cluster work", "trust": "inferred"},
            "open_questions": [{
                "text": text, "trust": "verbatim", "quote": text[:40],
                "importance": 9, "first_seen": created,
            }],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def _seed_match(project):
    # A matchable prior session plus a newer, unrelated one. The injection
    # path excludes whatever the SessionStart briefing already carried
    # (this project's LATEST checkpoint), so a single seeded session would
    # exclude itself.
    store.write_checkpoint(
        "S-old", _checkpoint_body(
            "S-old",
            "argocd selfHeal reverts any manual kubectl edit to the "
            "gateway deployment in prod", "2026-06-20T00:00:00Z"),
        project_dir=project)
    store.write_checkpoint(
        "S-latest", _checkpoint_body(
            "S-latest", "unrelated newer bookkeeping",
            "2026-06-28T00:00:00Z"),
        project_dir=project)


def _ledger(tmp_log_dir):
    path = tmp_log_dir / "recall-delivery.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_claude_code_record_only_writes_a_delivery_row_and_prints_nothing(
        mod, monkeypatch, capsys, real_subprocess, tmp_checkpoint_dir,
        tmp_log_dir):
    _seed_match("/repo/k8s")
    _payload(monkeypatch)  # default host/cwd match the seeded project
    _cli(mod, monkeypatch)
    assert _run(mod, "claude-code") == 0
    assert capsys.readouterr().out == ""
    rows = _ledger(tmp_log_dir)
    assert len(rows) == 1
    assert rows[0]["surface"] == "action-recall"
    # #1043: the row carries the live session the shell action ran in,
    # forwarded here through the shim's own --session flag.
    assert rows[0]["injected_into"] == SESSION


def test_codex_still_writes_no_row_for_the_same_matching_command(
        mod, monkeypatch, capsys, real_subprocess, tmp_checkpoint_dir,
        tmp_log_dir):
    _seed_match("/repo/k8s")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod, "codex") == 0
    assert capsys.readouterr().out == ""
    assert _ledger(tmp_log_dir) == []


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


def test_mcp_tool_flag_forwards_the_env_var_to_action_recall(
        mod, monkeypatch, capsys, spawn):
    # #1036 parity: an argv flag on this shim, not a shell env-var prefix on
    # the manifest command — the shim itself is responsible for exporting
    # the flag into the CLI subprocess's own env, the same accessor every
    # other host-aware env override in this codebase uses (project_env).
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert mod.main([str(HOOK), "claude-code", "--mcp-tool"]) == 0
    capsys.readouterr()
    _, kwargs = spawn[0]
    assert kwargs["env"]["DAIMON_MCP_TOOL_AVAILABLE"] == "1"


def test_mcp_tool_flag_forwards_claude_codes_own_tool_name_to_action_recall(
        mod, monkeypatch, capsys, spawn):
    # #1062: same seam as #1036's flag, next to it — the exact name Claude
    # Code's own tool list carries for the plugin's read-only MCP server.
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert mod.main([str(HOOK), "claude-code", "--mcp-tool"]) == 0
    capsys.readouterr()
    _, kwargs = spawn[0]
    assert (kwargs["env"]["DAIMON_MCP_TOOL_NAME"]
           == "mcp__plugin_daimon_daimon__daimon_recall")


def test_without_the_flag_the_env_carries_no_mcp_tool_name_var(
        mod, monkeypatch, capsys, spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert mod.main([str(HOOK), "claude-code"]) == 0
    capsys.readouterr()
    _, kwargs = spawn[0]
    assert "DAIMON_MCP_TOOL_NAME" not in (kwargs["env"] or {})


def test_codex_gets_no_tool_name_even_if_it_ever_carried_the_flag(
        mod, monkeypatch, capsys, spawn):
    # codex is `unsupported` in CAPS and returns before any subprocess runs
    # (see test_codex_still_costs_nothing_at_all), so NAMES never needs a
    # codex row today; this pins that a future codex row in CAPS alone would
    # not silently start naming a tool nobody resolved for it.
    assert "codex" not in mod.NAMES


def test_without_the_flag_the_env_carries_no_mcp_tool_var(
        mod, monkeypatch, capsys, spawn):
    _mode(mod, monkeypatch, "claude-code", "on")
    _payload(monkeypatch)
    _cli(mod, monkeypatch)
    assert _run(mod) == 0
    capsys.readouterr()
    _, kwargs = spawn[0]
    assert "DAIMON_MCP_TOOL_AVAILABLE" not in (kwargs["env"] or {})


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
    # `daimon_recall` listed. `--mcp-tool` tells the recall hint to name that
    # tool instead of the shell command — an argv flag, not a shell env-var
    # prefix, because a host that execs argv without a shell would treat
    # `VAR=1` as the program name and the hook would never start.
    assert hook["command"] == (
        'python3 "${CLAUDE_PLUGIN_ROOT}"/hook/daimon-action-recall.py '
        'claude-code --mcp-tool')
    # Five against the shim's own 1.5s subprocess budget: the shim decides
    # well before the host gives up, and a recall is never worth a stall.
    assert hook["timeout"] == 5
    assert "statusMessage" not in hook


def test_the_plugin_flags_mcp_tool_availability_on_the_prompt_recall_hook():
    # #1036: same flag, same reasoning, on the surface that actually prints
    # today (action-recall ships `record-only` for claude-code, so it never
    # emits a line yet, see CAPS).
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))["hooks"]
    hook = cfg["UserPromptSubmit"][0]["hooks"][0]
    assert hook["command"] == (
        'python3 "${CLAUDE_PLUGIN_ROOT}"/hook/daimon-prompt-recall.py '
        '--mcp-tool')


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
