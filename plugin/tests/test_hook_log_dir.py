"""#1234: the hook library resolves its log directory the way the CLI does.

serialize.log and the heartbeats are shared state. The hooks write the
spawn line and read both for the catch-up sweep and the in-flight guard;
the CLI reads both through config.log_dir() for `status`, `heal` and the
ledger fold. With the hooks hardcoding ~/.daimon/logs while the CLI honored
DAIMON_LOG_DIR, a host, a test suite or a sibling tool's boot probe running
with the variable redirected left its spawn lines in the REAL ledger and its
result lines and checkpoints in the redirected store, and `daimon status`
listed those sessions as started and never finished.

One resolver, `lib.log_dir()`, now reproduces config.log_dir() exactly
(scar 0043: quirks included), every hook read and write goes through it,
and there is no module-level constant left for a site to drift back to.
"""
import importlib.util
import os
import time
from pathlib import Path

import pytest

from daimon_briefing import config, ledger

SPAWN = ("session-start: spawned serialize for S-1 "
         "(reason: catch-up-orphan, project: /p/x) (transcript: /t/S-1.jsonl)")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch, _isolate_daimon_home) -> Path:
    """A sandbox HOME, armed BEFORE anything in this file imports or calls
    the library. These tests exist to prove that nothing lands under the
    default directory, which means they set DAIMON_LOG_DIR deliberately or
    leave it unset, so the suite-wide redirect in conftest is not what
    keeps them off the maintainer's real ~/.daimon/logs: HOME is. Without
    this, a library that still computed its path at import, or a test that
    never patched home, appended spawn lines to the real ledger, which is
    the exact class this file guards against. Ordered after conftest's
    isolation so the unset of DAIMON_LOG_DIR here is the final word."""
    sandbox = tmp_path / "home"
    sandbox.mkdir()
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR",
                       str(sandbox / ".daimon" / "checkpoints"))
    assert Path.home() == sandbox, "HOME did not take; refusing to touch disk"
    return sandbox


def _lib():
    """The shared hook library, loaded under its own module name (it is a
    standalone script the package cannot import)."""
    src = (Path(__file__).parent.parent / "daimon_briefing" / "_hooks"
           / "_daimon_hook_lib.py")
    spec = importlib.util.spec_from_file_location("_log_dir_hook_lib_under_test",
                                                  src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exercise_every_hook_site(lib, logs: Path) -> None:
    """Drive the three sites that touch the log directory: the serialize.log
    append, the heartbeat sweep and the failure read. Every assertion is
    against `logs`; the caller then asserts the default stayed untouched."""
    lib.log(SPAWN)
    assert (logs / "serialize.log").read_text(encoding="utf-8").endswith(
        f" {SPAWN}\n"), "the append went somewhere else"
    beats = logs / "heartbeats"
    beats.mkdir(parents=True, exist_ok=True)
    (beats / "S-live").write_text("slug", encoding="utf-8")
    assert lib._in_flight_stems() == {"S-live"}, "the sweep read another dir"
    with (logs / "serialize.log").open("a", encoding="utf-8") as f:
        f.write("error: boom (transcript: /t/S-failed.jsonl) after 3s\n")
    assert lib._failed_session_stems() == {"S-failed"}, \
        "the heal read came from another ledger"


def test_the_process_env_moves_every_hook_site_and_leaves_the_default_alone(
        tmp_path, monkeypatch, home):
    lib = _lib()
    redirected = tmp_path / "redirected-logs"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(redirected))
    assert config.log_dir() == redirected, "fixture never configured the CLI"
    _exercise_every_hook_site(lib, redirected)
    assert not (home / ".daimon").exists(), \
        "a hook site still wrote under the default while redirected"


def test_the_env_file_alone_moves_every_hook_site(tmp_path, monkeypatch, home):
    """The channel a GUI-launched host actually uses: no shell export reaches
    it, only ~/.daimon/env does (scar 0036). Set there and nowhere else the
    CLI moves, so the hooks must move with it."""
    lib = _lib()
    custom = tmp_path / "custom-logs"
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(f"export DAIMON_LOG_DIR={custom}\n", encoding="utf-8")
    assert config.log_dir() == custom, "fixture never configured the CLI"
    _exercise_every_hook_site(lib, custom)
    assert not (home / ".daimon").exists(), \
        "a hook site ignored the env file and wrote under the default"


def test_the_cli_ledger_folds_the_spawn_line_the_hook_wrote(tmp_path,
                                                            monkeypatch, home):
    """The symptom, end to end: the line the hook appends is the line the
    CLI's ledger fold reads, under the same redirect."""
    lib = _lib()
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "redirected-logs"))
    lib.log(SPAWN)
    text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    assert "S-1" in ledger._session_ledger(text, time.time())
    assert not (home / ".daimon").exists()


def test_hook_and_cli_agree_over_the_whole_probe_table(tmp_path, monkeypatch):
    """Behavioral equality, the idiom hung_after_seconds and crash_log_path
    already use: every probe asserts identity with config.log_dir() rather
    than with a literal this test would also have to get right. The strip
    axis and the env-file line forms are the two places a mirror drifts
    (scar 0043)."""
    lib = _lib()
    for raw in (None, "", "   ", "  /tmp/dlogs  ", "/tmp/dlogs", "~/dlogs"):
        monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
        if raw is not None:
            monkeypatch.setenv("DAIMON_LOG_DIR", raw)
        assert lib.log_dir() == config.log_dir(), repr(raw)

    monkeypatch.delenv("DAIMON_LOG_DIR", raising=False)
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    resolved = set()
    for line in (f"DAIMON_LOG_DIR={tmp_path}/plain",
                 f'DAIMON_LOG_DIR="{tmp_path}/quoted"',
                 f"DAIMON_LOG_DIR='{tmp_path}/single'",
                 f"export DAIMON_LOG_DIR={tmp_path}/exported",
                 f"  export   DAIMON_LOG_DIR =  {tmp_path}/spaced  ",
                 f"# DAIMON_LOG_DIR={tmp_path}/commented",
                 "DAIMON_LOG_DIR=",
                 "DAIMON_LOG_DIR=~/tilde",
                 f"NOT_THE_VAR={tmp_path}/other",
                 f"DAIMON_LOG_DIR={tmp_path}/first\nDAIMON_LOG_DIR={tmp_path}/last"):
        env_file.write_text(line + "\n", encoding="utf-8")
        assert lib.log_dir() == config.log_dir(), line
        resolved.add(lib.log_dir())
    assert len(resolved) > 1, \
        "every probe resolved alike: the env file was never read at all"


def test_the_crash_sink_sits_under_the_same_directory(tmp_path, monkeypatch):
    """One resolver for the whole directory: the crash sink is derived from
    it rather than resolved a second time beside it."""
    lib = _lib()
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "elsewhere"))
    assert lib.crash_log_path() == lib.log_dir() / "serialize-crash.log"


def test_the_library_keeps_no_module_level_log_directory():
    """A constant computed at import is a second answer to 'where', frozen
    at whatever HOME and env the import happened to see. With no constant
    there is nothing for a new site to reach for except the resolver."""
    assert not hasattr(_lib(), "LOG_DIR")
