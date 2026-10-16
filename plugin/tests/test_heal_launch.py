"""`heal.child_env` and `heal.launch` (#1179 PR 4, design D8).

The invariant: the heal child runs with exactly the configuration its parent
uses. The env is a copy of the parent's (or `base`), never filtered, so the
kill switch, the LLM settings and every path survive. ContextVar overrides do
not cross a process boundary, so the two dirs they can name are written over
the copy with the EFFECTIVE values."""

import os
import subprocess
import sys

from daimon_briefing import config, heal


def test_child_env_forwards_a_checkpoint_dir_override(tmp_path):
    other = tmp_path / "sandbox-checkpoints"
    with config.checkpoint_dir_override(other):
        env = heal.child_env({"PATH": "/bin", "DAIMON_CHECKPOINT_DIR": "/real"})
    assert env["DAIMON_CHECKPOINT_DIR"] == str(other)
    assert env["PATH"] == "/bin"


def test_child_env_keeps_every_inherited_name_unchanged():
    base = {"DAIMON_DISABLE": "1", "DAIMON_LLM_API_KEY": "k",
            "DAIMON_RECALL_DB": "/r.db", "DAIMON_TEAM_DIR": "/t",
            "DAIMON_AUTHOR": "a", "HOME": "/h"}
    env = heal.child_env(base)
    for name, value in base.items():
        assert env[name] == value, name


def test_child_env_is_a_copy_not_the_callers_mapping():
    base = {"A": "1"}
    env = heal.child_env(base)
    env["B"] = "2"
    assert base == {"A": "1"}


def test_child_env_settings_overwrite_the_copy():
    env = heal.child_env({"DAIMON_LLM_BACKEND": "litellm", "HOME": "/h"},
                         settings={"DAIMON_LLM_BACKEND": "command"})
    assert env["DAIMON_LLM_BACKEND"] == "command"
    assert env["HOME"] == "/h"


def test_child_env_effective_dirs_win_over_settings_and_base():
    env = heal.child_env({"DAIMON_LOG_DIR": "/base"},
                         settings={"DAIMON_LOG_DIR": "/elsewhere"})
    assert env["DAIMON_LOG_DIR"] == str(config.log_dir())


def test_child_env_defaults_to_the_process_env(monkeypatch):
    monkeypatch.setenv("DAIMON_X_PROBE", "1")
    monkeypatch.setenv("PLAIN_PROBE", "2")
    env = heal.child_env()
    assert env["DAIMON_X_PROBE"] == "1" and env["PLAIN_PROBE"] == "2"


def test_a_real_child_reads_the_parents_llm_key_and_kill_switch(monkeypatch):
    """The reported failure ("no LLM API key" in a healed session) reproduced
    one level down: a child started with `child_env()` must resolve the same
    config the parent does."""
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "parent-key")
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    probe = ("import sys; from daimon_briefing import config; "
             "sys.exit(0 if config.llm_api_key() == 'parent-key' "
             "and config.is_disabled() else 3)")
    assert heal.launch([sys.executable, "-c", probe], heal.child_env(),
                       wait=True) == 0


def test_launch_detaches_the_child_in_its_own_session(monkeypatch):
    seen = {}

    class P:
        def __init__(self, argv, **kw):
            seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(subprocess, "Popen", P)
    assert heal.launch(["x", "y"], {"A": "1"}) is None
    assert seen["argv"] == ["x", "y"]
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["env"] == {"A": "1"}
    assert seen["kw"]["stdin"] == subprocess.DEVNULL


def test_launch_wait_returns_the_exit_code():
    rc = heal.launch([sys.executable, "-c", "raise SystemExit(3)"],
                     dict(os.environ), wait=True)
    assert rc == 3
