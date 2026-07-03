"""Guard tests: no test may read or write the developer's real ~/.daimon.

These pin the autouse isolation in conftest. They take NO fixtures on purpose —
they assert the *default* state of every test, so a future serialize test that
forgets to request tmp_log_dir / tmp_checkpoint_dir still cannot leak into the
real ledger (issue #54).
"""

from pathlib import Path

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
