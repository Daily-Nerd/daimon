"""Multicycle experiment tests: import daimon_briefing from repo plugin/,
and never let a test touch the real ~/.daimon."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugin"))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / ".daimon"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(home / "no-such-env"))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(home / "checkpoints"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(home / "logs"))
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(home / "team"))
    monkeypatch.setenv("DAIMON_RECALL_DB", str(home / "recall.db"))
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    return home
