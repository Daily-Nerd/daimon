"""A held `dir_lock` is a lock on the LIVE sidecar at that path (#1411).

The lock is an flock on `<dir>/.pointer.lock`, opened by path. A fold
(`buckets._retire`) unlinks that sidecar while holding it, so a waiter that
had already opened it is later granted the flock on an inode no path names.
Granting is not enough: after the flock the lock must still be the one the
path names, or it excludes nobody.
"""

import fcntl
import os
from pathlib import Path

import pytest

from daimon_briefing import jsonl
from tests._lockhooks import FirstFlockHook as _FirstFlockHook


def _probe_is_excluded(sidecar: Path) -> bool:
    """True when a second opener of `sidecar` cannot take the lock."""
    probe = open(sidecar, "a+")
    try:
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        probe.close()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(jsonl, "_LOCK_TRIES", 3)
    monkeypatch.setattr(jsonl, "_LOCK_INTERVAL", 0.01)


def test_a_sidecar_unlinked_while_waiting_is_not_the_lock_that_is_held(
        tmp_path, monkeypatch, fast):
    sidecar = tmp_path / jsonl.LOCK_NAME
    monkeypatch.setattr(jsonl, "_fcntl", _FirstFlockHook(sidecar.unlink))
    with jsonl.dir_lock(tmp_path) as held:
        assert held is True
        # the held lock is on the sidecar the path names NOW
        assert sidecar.exists()
        assert _probe_is_excluded(sidecar)


def test_a_directory_removed_while_waiting_is_not_held(
        tmp_path, monkeypatch, fast):
    d = tmp_path / "gone"
    d.mkdir()

    def fold():
        (d / jsonl.LOCK_NAME).unlink()
        d.rmdir()

    monkeypatch.setattr(jsonl, "_fcntl", _FirstFlockHook(fold))
    with jsonl.dir_lock(d) as held:
        assert held is False
    assert not d.exists()


def test_a_reborn_directory_whose_writer_holds_the_new_sidecar_is_not_ours(
        tmp_path, monkeypatch, fast):
    d = tmp_path / "reborn"
    d.mkdir()
    writer = []

    def fold_and_rebirth():
        (d / jsonl.LOCK_NAME).unlink()
        d.rmdir()
        d.mkdir()
        fh = open(d / jsonl.LOCK_NAME, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        writer.append(fh)

    monkeypatch.setattr(jsonl, "_fcntl", _FirstFlockHook(fold_and_rebirth))
    try:
        with jsonl.dir_lock(d) as held:
            # the old inode is grantable, but it is not the writer's lock
            assert held is False
        assert _probe_is_excluded(d / jsonl.LOCK_NAME)
    finally:
        writer[0].close()


def test_an_undisturbed_lock_is_still_held_and_excludes(tmp_path):
    with jsonl.dir_lock(tmp_path) as held:
        assert held is True
        assert _probe_is_excluded(tmp_path / jsonl.LOCK_NAME)
    assert os.path.exists(tmp_path / jsonl.LOCK_NAME)
