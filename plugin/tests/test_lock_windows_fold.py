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


@pytest.fixture
def windows_fs(monkeypatch):
    prim, fake = windows_lock()
    monkeypatch.setattr(jsonl, "_lock", prim)

    def pinned(path) -> bool:
        """A live lock handle on `path`, or on a file inside it."""
        try:
            st = os.stat(path)
            keys = {(st.st_dev, st.st_ino)}
            if os.path.isdir(path):
                for name in os.listdir(path):
                    s = os.stat(os.path.join(path, name))
                    keys.add((s.st_dev, s.st_ino))
        except OSError:
            return False
        return any(r[0] in keys and fake._live(r) is not None
                   for r in list(fake._held))

    real_rename, real_unlink = os.rename, pathlib.Path.unlink

    def rename(src, dst, *a, **k):
        if pinned(src):
            raise PermissionError(13, "in use by another handle", str(src))
        return real_rename(src, dst, *a, **k)

    def unlink(self, *a, **k):
        if pinned(self):
            raise PermissionError(13, "in use by another handle", str(self))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    return fake


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
