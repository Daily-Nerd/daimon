"""A fold must work where an open handle pins its file (#1401).

Windows refuses to unlink a file, or rename the directory around it, while any
handle to it is open. The fold (`buckets._retire`, the `migrate` rename) does
both while it holds the bucket's lock, so on Windows the holder has to let go of
its sidecar handle first. These tests run the real fold over the Windows lock
primitive and make the filesystem refuse like Windows does, so the failure
shows up on any platform.
"""

import json
import os
import pathlib

import pytest

from daimon_briefing import buckets, jsonl, store
from tests._lockhooks import windows_lock
from tests.test_bucket_fail_closed import _bucket, _events
from tests.test_bucket_merge import _dir
from tests.test_bucket_migration import _plant, _populate_legacy, _row


class WindowsFs:
    """The filesystem as Windows has it: a path with an open handle, or a
    directory with one inside, cannot be renamed, and a file with one cannot
    be unlinked. The handles are this process's live lock handles (the fake
    `msvcrt` knows them) and FOREIGN ones (`hold`): another process's waiter,
    which no code here can close."""

    def __init__(self, fake):
        self.fake = fake
        self._foreign: set = set()

    def hold(self, path) -> None:
        self._foreign.add(os.path.realpath(path))

    def pinned(self, path) -> bool:
        real = os.path.realpath(path)
        if any(f == real or f.startswith(real + os.sep)
               for f in self._foreign):
            return True
        try:
            st = os.stat(path)
            keys = {(st.st_dev, st.st_ino)}
            if os.path.isdir(path):
                for name in os.listdir(path):
                    s = os.stat(os.path.join(path, name))
                    keys.add((s.st_dev, s.st_ino))
        except OSError:
            return False
        return any(r[0] in keys and self.fake._live(r) is not None
                   for r in list(self.fake._held))


@pytest.fixture
def windows_fs(monkeypatch):
    prim, fake = windows_lock()
    monkeypatch.setattr(jsonl, "_lock", prim)
    fs = WindowsFs(fake)
    real_rename, real_unlink = os.rename, pathlib.Path.unlink

    def rename(src, dst, *a, **k):
        if fs.pinned(src):
            raise PermissionError(13, "in use by another handle", str(src))
        return real_rename(src, dst, *a, **k)

    def unlink(self, *a, **k):
        if fs.pinned(self):
            raise PermissionError(13, "in use by another handle", str(self))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    return fs


def test_a_merge_removes_the_folded_bucket(tmp_checkpoint_dir, windows_fs):
    _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert record["complete"] is True
    assert _events("-b") == {"g1", "b1"}
    assert not _dir("-g").exists()


def test_a_migrate_rename_moves_the_bucket(tmp_checkpoint_dir, windows_fs,
                                           tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "ln").symlink_to(real)
    link = tmp_path / "ln"
    legacy = _populate_legacy(link, [("S1", "2026-09-01T00:00:00Z")])
    _plant(legacy, {"events.jsonl": _row("e1")})
    record = buckets.migrate(link)
    assert record["mode"] == "rename"
    assert not legacy.exists()
    target = tmp_checkpoint_dir / store.project_bucket(real)
    assert json.loads((target / "events.jsonl").read_text().splitlines()[0])[
        "event_id"] == "e1"


def test_a_foreign_handle_on_the_sidecar_makes_the_fold_fail_safe(
        tmp_checkpoint_dir, windows_fs):
    """Another process has the legacy sidecar open (a waiter). Giving up our
    own handle cannot help: the rename is refused. The merge must say the
    bucket is NOT gone, leave it where it is, and lose nothing."""
    _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    windows_fs.hold(_dir("-g") / jsonl.LOCK_NAME)
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert record["complete"] is False
    assert _dir("-g").is_dir()
    assert _events("-b") == {"g1", "b1"}
    assert not any(_dir("-g").glob("*.tmp"))


@pytest.mark.parametrize("src, dst", [("-a", "-b"), ("-b", "-a")])
def test_a_second_merge_of_the_pair_cannot_slip_in_while_the_first_retires(
        tmp_checkpoint_dir, windows_fs, monkeypatch, src, dst):
    """After the first run gives its legacy lock up it still holds the
    target's, and a merge needs both: a second run of the same pair is
    refused, so it can never append a receipt the first run then overwrites
    (the order that would read a gone bucket as incomplete)."""
    monkeypatch.setattr(jsonl, "_LOCK_TRIES", 3)
    monkeypatch.setattr(jsonl, "_LOCK_INTERVAL", 0.01)
    _bucket(src, [f"{src}1"])
    _bucket(dst, [f"{dst}1"])
    real_move_aside = store.move_aside
    seen = {}

    def second_run_in_the_window(bucket):
        before = len(buckets.latest_rows())
        try:
            buckets.merge(src, dst, apply=True, force=True)
            seen["second"] = "ran"
        except buckets.MigrationError as exc:
            seen["second"] = str(exc)
        seen["rows_added"] = len(buckets.latest_rows()) - before
        return real_move_aside(bucket)

    monkeypatch.setattr(store, "move_aside", second_run_in_the_window)
    record = buckets.merge(src, dst, apply=True, force=True)
    assert "could not lock" in seen["second"]
    assert seen["rows_added"] == 0
    assert record["complete"] is True
    assert not _dir(src).exists()
