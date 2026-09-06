"""#943 slice 3: the pre-action hooks, run the way the hosts run them.

These are the first daimon hooks that can fail a host action, so nothing
here imports them. Each test spawns the script in a fresh interpreter with a
payload on stdin, exactly as Claude Code and Codex do, and reads back what
the host would read: stdout, stderr and the exit code.

Two disciplines every test holds to. stdout is empty or ONE JSON object,
pinned the way the Gemini hooks are (`json.loads(proc.stdout)` or
`proc.stdout == ""`), because a host that cannot parse the hook's output
learns nothing from it. And the exit code is 0 on every path: exit 2 is a
documented unconditional block on both hosts, so an escaping exception that
happened to exit non-zero would block an action no check ever judged.

The manifest under test is written by `checks.sync` from a ruling ratified
in process through `refutations` on a human channel. A hand-written manifest
would exercise a shape nobody produces.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daimon_briefing import config, refutations

HOOK_DIR = Path(__file__).parents[2] / "hook"
CLAUDE_HOOK = HOOK_DIR / "daimon-pre-action.py"
CODEX_HOOK = HOOK_DIR / "daimon-codex-pre-action.py"
CORE = "checks_host.py"
RUNTIME = "checks_runtime.py"

CLEAN = "#!/bin/sh\nexit 0\n"
VIOLATION = "#!/bin/sh\necho 'no em-dash in a public body' >&2\nexit 1\n"
MATCH = "gh pr create"


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _arm(project, *, body=VIOLATION, intent="warn", subject="public posts",
         scope="publishing", match=MATCH):
    ruling_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:943"], channel="cli-agent",
        check={"match": match, "body": body, "intent": intent},
        project_dir=str(project))
    refutations.ratify(ruling_id, channel="ui", check_sha256=_sha(body),
                       project_dir=str(project))
    return ruling_id


def _run(script, payload, tmp_path):
    """Spawn the hook the way the host does: its own interpreter, the payload
    on stdin, no daimon_briefing anywhere on the path. A non-dict payload is
    passed through as a raw string, which is how the unparseable-stdin cases
    are driven."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(script)], input=text, capture_output=True,
        text=True, timeout=60,
        env={**os.environ, "HOME": str(tmp_path)})


def _payload(command, cwd, *, host="claude-code"):
    return {"tool_name": "Bash" if host == "claude-code" else "shell",
            "tool_input": {"command": command}, "cwd": str(cwd)}


def _assert_host_contract(proc):
    """Every path, both hosts: exit 0, stderr silent, stdout empty or one
    parseable JSON object."""
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    if proc.stdout:
        assert json.loads(proc.stdout)
    return proc.stdout


def _log_rows():
    path = config.log_dir() / "checks.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


HOOKS = [("claude-code", CLAUDE_HOOK), ("codex", CODEX_HOOK)]
IDS = ["claude-code", "codex"]


# ---- the shape of a decision, through the real scripts --------------------


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_violation_under_enforce_denies_on_both_hosts(host, script,
                                                        tmp_path):
    """The one path measured on both hosts: JSON deny, exit 0. The hook never
    relies on exit 2, which is documented as an unconditional block and would
    fire on a crash as readily as on a decision."""
    ruling_id = _arm(tmp_path, intent="enforce")
    proc = _run(script, _payload(MATCH, tmp_path, host=host), tmp_path)
    data = json.loads(_assert_host_contract(proc))
    out = data["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"] == \
        f"{ruling_id}: no em-dash in a public body"


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_clean_check_says_nothing_at_all(host, script, tmp_path):
    _arm(tmp_path, body=CLEAN, intent="enforce")
    proc = _run(script, _payload(MATCH, tmp_path, host=host), tmp_path)
    assert _assert_host_contract(proc) == ""
    assert [r["outcome"] for r in _log_rows()] == ["clean"]


def test_a_warn_reaches_claude_code_as_an_allow_with_a_message(tmp_path):
    ruling_id = _arm(tmp_path, intent="warn")
    proc = _run(CLAUDE_HOOK, _payload(MATCH, tmp_path), tmp_path)
    data = json.loads(_assert_host_contract(proc))
    assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert data["systemMessage"] == f"{ruling_id}: no em-dash in a public body"
    assert _log_rows()[0]["decision_emitted"] == "warn"


def test_the_same_warn_is_recorded_and_not_shown_on_codex(tmp_path):
    """Codex documents no warn channel. The run still happened and the log
    still says so, at the mode the host could actually deliver."""
    _arm(tmp_path, intent="warn")
    proc = _run(CODEX_HOOK, _payload(MATCH, tmp_path, host="codex"), tmp_path)
    assert _assert_host_contract(proc) == ""
    row = _log_rows()[0]
    assert row["host"] == "codex"
    assert row["mode"] == "record-only"
    assert row["outcome"] == "violation"
    assert row["decision_emitted"] == "allow"


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_record_only_check_never_reaches_the_host(host, script, tmp_path):
    _arm(tmp_path, intent="record-only")
    proc = _run(script, _payload(MATCH, tmp_path, host=host), tmp_path)
    assert _assert_host_contract(proc) == ""
    assert _log_rows()[0]["mode"] == "record-only"


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_an_unresolved_subject_denies_under_enforce(host, script, tmp_path):
    """Spec 2.3: unresolved is never rendered as clean. daimon could not read
    what the action sends, so under enforce it cannot let it through."""
    ruling_id = _arm(tmp_path, body=CLEAN, intent="enforce")
    command = f"{MATCH} --body-file missing.md"
    proc = _run(script, _payload(command, tmp_path, host=host), tmp_path)
    data = json.loads(_assert_host_contract(proc))
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith(f"{ruling_id}: file-missing: ")
    row = _log_rows()[0]
    assert row["outcome"] == "unresolved"
    assert row["cause"] == "file-missing"
    assert row["decision_emitted"] == "deny"


# ---- nothing armed, and nothing to say ------------------------------------


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_no_manifest_allows_and_leaves_a_row_that_says_so(host, script,
                                                          tmp_path):
    proc = _run(script, _payload(MATCH, tmp_path, host=host), tmp_path)
    assert _assert_host_contract(proc) == ""
    rows = _log_rows()
    assert [r["cause"] for r in rows] == ["no-manifest"]
    assert rows[0]["decision_emitted"] == "allow"
    assert rows[0]["host"] == host


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_ruling_armed_for_another_project_is_no_match(host, script,
                                                        tmp_path):
    other, here = tmp_path / "other", tmp_path / "here"
    other.mkdir()
    here.mkdir()
    _arm(other, intent="enforce")
    proc = _run(script, _payload(MATCH, here, host=host), tmp_path)
    assert _assert_host_contract(proc) == ""
    assert [r["cause"] for r in _log_rows()] == ["no-match"]


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_tool_that_is_not_a_shell_action_is_ignored_entirely(host, script,
                                                               tmp_path):
    """No output and no row. A row on every file edit would turn the firing
    log into a transcript of the session."""
    _arm(tmp_path, intent="enforce")
    proc = _run(script, {"tool_name": "Edit",
                         "tool_input": {"command": MATCH},
                         "cwd": str(tmp_path)}, tmp_path)
    assert _assert_host_contract(proc) == ""
    assert _log_rows() == []


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
@pytest.mark.parametrize("payload", ["", "not json at all", "[]", "null",
                                     '{"tool_name": "Bash"}'])
def test_an_unusable_payload_is_a_silent_allow(host, script, payload,
                                               tmp_path):
    """Scar 0068 generalised: a host payload field is a claim. Unparseable
    stdin, a payload that is not an object, and an object with no command all
    describe an action daimon cannot see, which is an allow and never a
    crash."""
    _arm(tmp_path, intent="enforce")
    proc = _run(script, payload, tmp_path)
    assert _assert_host_contract(proc) == ""
    assert _log_rows() == []


# ---- partial installs -----------------------------------------------------


def _stray(script, tmp_path, *, include=()):
    """The script in a directory holding only `include` — the shape a partial
    or half-upgraded install leaves behind."""
    home = tmp_path / "stray"
    home.mkdir(exist_ok=True)
    dest = home / script.name
    dest.write_bytes(script.read_bytes())
    for name in include:
        (home / name).write_bytes((HOOK_DIR / name).read_bytes())
    return dest


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_a_hook_with_no_adapter_core_beside_it_says_nothing(host, script,
                                                            tmp_path):
    """Fail open and silent. There is nothing to check with and nothing to
    write a row with, and a traceback on stderr before every shell action is
    worse than the missing check it would be reporting."""
    _arm(tmp_path, intent="enforce")
    proc = _run(_stray(script, tmp_path), _payload(MATCH, tmp_path, host=host),
                tmp_path)
    assert _assert_host_contract(proc) == ""
    assert _log_rows() == []


def test_a_missing_runtime_tells_claude_code_that_nothing_is_enforced(
        tmp_path):
    """The half-upgraded install: the core shipped, the runtime did not. On a
    host with a message channel, saying so is the difference between a check
    that passed and a check that never ran."""
    _arm(tmp_path, intent="enforce")
    proc = _run(_stray(CLAUDE_HOOK, tmp_path, include=(CORE,)),
                _payload(MATCH, tmp_path), tmp_path)
    data = json.loads(_assert_host_contract(proc))
    assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert data["systemMessage"] == \
        "daimon: check runtime missing, nothing enforced"
    assert _log_rows() == []


def test_the_same_missing_runtime_is_silent_on_codex(tmp_path):
    """Codex renders no message channel, so a diagnostic addressed to one is
    noise the operator never sees. The row would be the honest surface, and
    there is no runtime to write it with."""
    _arm(tmp_path, intent="enforce")
    proc = _run(_stray(CODEX_HOOK, tmp_path, include=(CORE,)),
                _payload(MATCH, tmp_path, host="codex"), tmp_path)
    assert _assert_host_contract(proc) == ""
    assert _log_rows() == []


def test_the_core_and_the_runtime_together_are_a_working_hook(tmp_path):
    """The converse of the two above: copied out of the repo entirely, with
    both siblings, the hook still decides. That is what `hooks install` ships
    and what proves the file-location load does not depend on this tree."""
    _arm(tmp_path, intent="enforce")
    proc = _run(_stray(CODEX_HOOK, tmp_path, include=(CORE, RUNTIME)),
                _payload(MATCH, tmp_path, host="codex"), tmp_path)
    data = json.loads(_assert_host_contract(proc))
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- the file header says what this hook can do ---------------------------


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_the_header_says_this_hook_can_fail_a_host_action(host, script):
    """Every other daimon hook is unable to affect the session it observes.
    Someone reading this one for the first time has to learn that from the
    file, not from an incident."""
    head = script.read_text(encoding="utf-8")[:2000]
    assert "fail a host action" in head.lower()
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


@pytest.mark.parametrize("host,script", HOOKS, ids=IDS)
def test_the_script_names_its_profile_and_nothing_else(host, script):
    """The thin-script contract: a host is a row plus a name. A script that
    made its own decisions would be a second pipeline, which is the thing
    this slice exists to not have."""
    text = script.read_text(encoding="utf-8")
    assert f'main("{host}")' in text
    assert "permissionDecision" not in text
    assert "hookSpecificOutput" not in text
