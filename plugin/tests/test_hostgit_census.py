"""Which modules still spawn a process, name `git`/`gh`, or touch a `.git` path.

`daimon_briefing.hostgit` is the one module that forks git or gh, builds a
`git`/`gh` argv, or reads a `.git` entry on a project directory. Every other
site the scan finds must be listed in `ALLOWLIST` with the reason it is not a
host git call. The list is compared by equality, so a new unlisted site fails
and so does a listed site that no longer exists. A site is
(module, enclosing function, primitive).

Primitives: a call of `subprocess.run/Popen/call/check_call/check_output`,
`os.system`, `os.exec*`, `os.spawn*`, `shutil.which`; the string literal
"git" or "gh" inside a list or tuple (an argv); and ".git" as a path segment
(`... / ".git"`, `Path(..., ".git")`, `os.path.join(..., ".git")`,
`.joinpath(..., ".git")`).

Scanned: every module of the `LAYER` table (tests/test_read_layers.py) except
hostgit.py itself, plus the standalone host scripts, which sit outside the
import graph: `daimon_briefing/_hooks/` and the repo's `hook/`. A source file
that cannot be parsed fails loudly rather than being skipped.
"""

from pathlib import Path

import pytest

from daimon_briefing import testing

from .test_read_layers import LAYER, PLUGIN, modules

HOSTGIT = "daimon_briefing/hostgit.py"
REPO = PLUGIN.parent

_CLI = "runs the `daimon` CLI as a child process; git is never named"
_TEAM = "team dir only, never a project dir"
_WHICH = "looks up a non-git executable on PATH; no git or gh involved"

# Standalone host scripts: `hook/` is the source, `daimon_briefing/_hooks/` its
# packaged copy (scripts/sync_hooks.py keeps them equal), so one entry covers
# both. (file, function, primitive) -> why it is not a host git call.
_HOOK_SITES: dict[tuple[str, str, str], str] = {
    ("_daimon_hook_lib.py", "_parent_argv", "subprocess.run"):
        "runs `ps` to read the parent process command line",
    ("_daimon_hook_lib.py", "has_team_remote", "path:.git"):
        _TEAM + " (cheap gate for the opportunistic team sync)",
    ("_daimon_hook_lib.py", "resolve_cli", "shutil.which"):
        "locates the `daimon` CLI on PATH",
    ("_daimon_hook_lib.py", "run_brief", "subprocess.run"): _CLI,
    ("_daimon_hook_lib.py", "spawn_heal", "subprocess.Popen"): _CLI,
    ("_daimon_hook_lib.py", "spawn_serialize", "subprocess.Popen"): _CLI,
    ("_daimon_hook_lib.py", "spawn_team_sync", "subprocess.Popen"): _CLI,
    ("checks_runtime.py", "_run", "subprocess.Popen"):
        "runs an armed check body with `sh`, in the ruling's own cwd",
    ("daimon-action-recall.py", "main", "subprocess.run"): _CLI,
    ("daimon-codex-user-prompt-submit.py", "_deliver", "subprocess.run"): _CLI,
    ("daimon-codex-user-prompt-submit.py", "main", "subprocess.run"): _CLI,
    ("daimon-kimi-user-prompt-submit.py", "_deliver", "subprocess.run"): _CLI,
    ("daimon-kimi-user-prompt-submit.py", "_recall", "subprocess.run"): _CLI,
    ("daimon-mcp-serve.py", "main", "os.execv"):
        "replaces the process image with `daimon mcp serve`",
    ("daimon-windsurf-hooks.py", "_arm_finalizer", "subprocess.Popen"):
        "re-launches this same script detached to finalize a transcript",
}
# Only `hook/` carries this one (the packaged copy does not ship it).
_HOOK_ONLY_SITES: dict[tuple[str, str, str], str] = {
    ("daimon-prompt-recall.py", "_deliver", "subprocess.run"): _CLI,
    ("daimon-prompt-recall.py", "main", "subprocess.run"): _CLI,
}

# (module, function, primitive) -> why it is not a host git call.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("daimon_briefing/checks_runtime.py", "_run", "subprocess.Popen"):
        "runs an armed check body with `sh`, in the ruling's own cwd",
    ("daimon_briefing/configure.py", "status", "shutil.which"):
        "reports whether the `claude` CLI is on PATH",
    ("daimon_briefing/heal.py", "launch", "subprocess.Popen"):
        "starts the detached `daimon heal` child",
    ("daimon_briefing/host_detect.py", "detect", "shutil.which"):
        "detects which host CLIs are installed (a table of host binaries)",
    ("daimon_briefing/llm.py", "_missing_binary", "shutil.which"):
        "checks the configured LLM command exists",
    ("daimon_briefing/llm.py", "_resolve_command", "shutil.which"):
        "resolves the LLM CLI executable",
    ("daimon_briefing/llm.py", "_run_command", "subprocess.run"):
        "the one LLM CLI subprocess boundary",
    ("daimon_briefing/receipts.py", "_derive_pubkey_x", "subprocess.run"):
        "runs `openssl` to derive an Ed25519 public key",
    ("daimon_briefing/receipts.py", "_resolve_cli", "shutil.which"):
        "locates the vitni CLI",
    ("daimon_briefing/receipts.py", "_run_cli", "subprocess.run"):
        "runs the vitni CLI",
    ("daimon_briefing/recall.py", "_scan_sources", "path:.git"): _TEAM,
    ("daimon_briefing/store.py", "_own_author_dirs_everywhere", "path:.git"):
        _TEAM,
    ("daimon_briefing/store.py", "_team_write_slugs", "path:.git"): _TEAM,
    ("daimon_briefing/store.py", "read_team", "path:.git"): _TEAM,
    ("daimon_briefing/teamsync.py", "_recover_wedge", "path:.git"):
        _TEAM + " (sidecar merge and lock state files)",
    ("daimon_briefing/teamsync.py", "list_remotes", "path:.git"): _TEAM,
    ("daimon_briefing/testing.py", "_refuse_root", "path:.git"):
        "test-sandbox guard: refuses a sandbox root inside a git work tree; "
        "answers nothing about project identity",
    ("daimon_briefing/worldcheck.py", "_run_probes", "subprocess.Popen"):
        "runs the vitni verifier probes; gh probes go through hostgit.gh_popen",
}
ALLOWLIST |= {
    (f"{prefix}/{name}", fn, prim): reason
    for prefix in ("daimon_briefing/_hooks", "hook")
    for (name, fn, prim), reason in _HOOK_SITES.items()
}
ALLOWLIST |= {
    (f"hook/{name}", fn, prim): reason
    for (name, fn, prim), reason in _HOOK_ONLY_SITES.items()
}


def _scanned_files():
    files = {key: path for key, path in modules().items() if key != HOSTGIT}
    for directory, prefix in ((PLUGIN / "daimon_briefing" / "_hooks",
                               "daimon_briefing/_hooks"),
                              (REPO / "hook", "hook")):
        for path in sorted(directory.glob("*.py")):
            files[f"{prefix}/{path.name}"] = path
    return files


def test_only_hostgit_spawns_git_or_names_a_git_argv_outside_the_allowlist():
    # Raises on an unlisted site (route it through daimon_briefing.hostgit, or
    # list it with a reason) and on a listed site that no longer exists.
    testing.assert_single_git_owner(_scanned_files(), ALLOWLIST)


def test_every_allowlist_entry_has_a_reason():
    assert all(reason.strip() for reason in ALLOWLIST.values())


def test_the_scan_covers_hostgit_the_hooks_and_the_package():
    keys = set(_scanned_files())
    assert "daimon_briefing/config.py" in keys
    assert "daimon_briefing/_hooks/_daimon_hook_lib.py" in keys
    assert "hook/_daimon_hook_lib.py" in keys
    assert HOSTGIT in modules() and HOSTGIT not in keys


def test_hostgit_is_a_write_layer_module():
    assert LAYER[HOSTGIT] == "write"


def _sites(tmp_path, source: str) -> set[tuple[str, str]]:
    path = tmp_path / "m.py"
    path.write_text(source, encoding="utf-8")
    found = testing.git_spawn_sites({"m.py": path})
    assert {module for module, _fn, _prim in found} <= {"m.py"}
    return {(fn, prim) for _module, fn, prim in found}


def test_the_exemption_is_not_vacuous_hostgit_really_spawns_git():
    found = testing.git_spawn_sites({HOSTGIT: PLUGIN / HOSTGIT})
    primitives = {prim for _module, _fn, prim in found}
    assert {"subprocess.run", "subprocess.Popen", "shutil.which",
            "literal:git", "path:.git"} <= primitives


def test_the_testing_module_stays_importable_without_pytest():
    import subprocess
    import sys
    code = ("import sys; sys.modules['pytest'] = None; "
            "from daimon_briefing import testing; "
            "assert testing.git_spawn_sites and testing.assert_single_git_owner")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=60, cwd=PLUGIN)
    assert done.returncode == 0, done.stderr


# ---- negative control: the census must be able to fail --------------------


def test_negative_control_a_fake_git_spawn_is_found(tmp_path):
    fake = (
        "import subprocess, os, shutil\n"
        "from pathlib import Path\n"
        "def leak(p):\n"
        "    subprocess.run(['git', '-C', p, 'status'])\n"
        "    subprocess.Popen(('gh', 'pr', 'view'))\n"
        "    shutil.which('git')\n"
        "    os.execv('/bin/x', ['x'])\n"
        "    return (Path(p) / '.git').exists(), Path(p, '.git'), p / '.git'\n"
    )
    assert _sites(tmp_path, fake) == {
        ("leak", "subprocess.run"), ("leak", "literal:git"),
        ("leak", "subprocess.Popen"), ("leak", "literal:gh"),
        ("leak", "shutil.which"), ("leak", "os.execv"),
        ("leak", "path:.git"),
    }


def test_negative_control_a_stray_git_spawn_fails_through_the_exported_check(
        tmp_path):
    (tmp_path / "rogue.py").write_text(
        "import subprocess\n"
        "def f():\n"
        "    return subprocess.run(['git', 'status'])\n", encoding="utf-8")
    paths = {"daimon_briefing/rogue.py": tmp_path / "rogue.py"}
    with pytest.raises(AssertionError) as raised:
        testing.assert_single_git_owner(paths, ALLOWLIST)
    message = str(raised.value)
    assert "daimon_briefing/rogue.py" in message and "subprocess.run" in message
    # the same tree passes once the site is listed, and the listed-but-missing
    # side of the comparison fails when the site goes away
    listed = {("daimon_briefing/rogue.py", "f", "subprocess.run"): "x",
              ("daimon_briefing/rogue.py", "f", "literal:git"): "x"}
    testing.assert_single_git_owner(paths, listed)
    (tmp_path / "rogue.py").write_text("def f():\n    return 1\n",
                                       encoding="utf-8")
    with pytest.raises(AssertionError, match="stale"):
        testing.assert_single_git_owner(paths, listed)


def test_a_syntax_error_in_a_scanned_file_is_loud(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        testing.git_spawn_sites({"broken.py": broken})


def test_innocent_code_is_quiet(tmp_path):
    assert _sites(
        tmp_path,
        "import json\n"
        "def f(p):\n"
        "    s = 'x.git'\n"
        "    return json.dumps(['claude', 'codex']), s[: -len('.git')]\n"
    ) == set()


def test_paths_used_here_exist():
    assert Path(PLUGIN / HOSTGIT).is_file()
