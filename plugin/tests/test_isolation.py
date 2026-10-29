"""Guard tests: no test may read or write the developer's real ~/.daimon.

These pin the autouse isolation in conftest. They take NO fixtures on purpose —
they assert the *default* state of every test, so a future serialize test that
forgets to request tmp_log_dir / tmp_checkpoint_dir still cannot leak into the
real ledger (issue #54).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from daimon_briefing import config


def test_log_dir_isolated_from_real_home():
    # The serialize result ledger must never resolve to the developer's home.
    assert config.log_dir() != Path.home() / ".daimon" / "logs"


def test_checkpoint_dir_isolated_from_real_home():
    assert config.checkpoint_dir() != Path.home() / ".daimon" / "checkpoints"


def test_team_dir_isolated_from_real_home():
    # The #111 team mirror must never resolve to the developer's real home, or a
    # dual-write test would leak checkpoints into it (and read_team would read it).
    # Relies on conftest's autouse DAIMON_TEAM_DIR redirection: remove that
    # fixture line and THIS test fails — that failure is the guard working.
    assert config.team_dir() != Path.home() / ".daimon" / "team"


def test_recall_db_isolated_from_real_home():
    # The #112 recall index must never resolve to the developer's real home, or
    # a recall test would rebuild/clobber the real derived index (and scan the
    # real checkpoint history into test assertions). Relies on conftest's
    # autouse DAIMON_RECALL_DB redirection — remove that line and THIS fails.
    assert config.recall_db() != Path.home() / ".daimon" / "recall.db"


# #1264: the per-path redirects above only cover the paths someone remembered.
# The invariant is on HOME itself: the whole run lives under a throwaway home,
# so any path derived from it, at import time or in a child process, lands in
# the throwaway directory.


def _account_home() -> Path:
    # The password database answers independently of $HOME, so a redirected
    # HOME cannot hide the developer's real one from this check.
    pwd = pytest.importorskip("pwd")
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def test_path_home_is_not_the_real_home():
    real = _account_home()
    home = Path.home().resolve()
    assert home != real
    assert real not in home.parents
    assert Path(os.path.expanduser("~")).resolve() == home


def test_subprocess_inherits_the_throwaway_home():
    out = subprocess.run(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert Path(out).resolve() == Path.home().resolve()
    assert Path(out).resolve() != _account_home()


def test_every_home_derived_default_lands_outside_the_real_home(monkeypatch):
    # Drop the per-path overrides so the defaults resolve from HOME alone.
    for var in ("DAIMON_CHECKPOINT_DIR", "DAIMON_LOG_DIR", "DAIMON_TEAM_DIR",
                "DAIMON_RECALL_DB", "DAIMON_RECALL_SEEN_DIR",
                "DAIMON_WINDSURF_DIR", "DAIMON_CHECKS_DIR"):
        monkeypatch.delenv(var)
    real = _account_home()
    for resolved in (config.checkpoint_dir(), config.log_dir(),
                     config.team_dir(), config.recall_db()):
        assert real not in resolved.resolve().parents
