"""One lock contract, run against BOTH platform primitives (#1401).

`jsonl` picks one lock primitive when it is imported: `fcntl.flock` on POSIX,
`msvcrt.locking` on Windows. Everything that locks (`dir_lock`, the heal lock)
goes through it, so the contract below is the same on both. The `native`
parameter is whatever this platform selected (the Windows CI job runs it on
the real thing); `windows` is the Windows primitive over a stand-in for
`msvcrt`, so the selection and the byte-range protocol are exercised on every
platform.
"""

import errno
import tempfile

import pytest

from daimon_briefing import heal, jsonl
from tests._lockhooks import FakeMsvcrt, windows_lock


@pytest.fixture(params=["native", "windows"])
def primitive(request, monkeypatch):
    if request.param == "windows":
        prim, _fake = windows_lock()
        monkeypatch.setattr(jsonl, "_lock", prim)
    return jsonl._lock


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(jsonl, "_LOCK_TRIES", 3)
    monkeypatch.setattr(jsonl, "_LOCK_INTERVAL", 0.01)


def _probe_is_excluded(sidecar) -> bool:
    """True when a second opener of `sidecar` cannot take the lock."""
    probe = open(sidecar, "a+")
    try:
        try:
            jsonl.lock_fd(probe.fileno())
            jsonl.unlock_fd(probe.fileno())
            return False
        except OSError:
            return True
    finally:
        probe.close()


# ---- the selection ----------------------------------------------------------


def test_a_platform_with_only_msvcrt_has_a_lock():
    """The bug: no `fcntl` meant no lock at all, so every verb that deletes
    refused for good."""
    prim = jsonl._select_primitive(fcntl=None, msvcrt=FakeMsvcrt())
    assert prim is not None


def test_a_platform_with_neither_module_has_no_lock():
    assert jsonl._select_primitive(fcntl=None, msvcrt=None) is None


def test_locking_available_follows_the_selected_primitive(monkeypatch):
    prim, _ = windows_lock()
    monkeypatch.setattr(jsonl, "_lock", prim)
    assert jsonl.locking_available() is True
    monkeypatch.setattr(jsonl, "_lock", None)
    assert jsonl.locking_available() is False


def test_this_platform_selected_a_primitive():
    assert jsonl.locking_available() is True


def test_the_windows_primitive_locks_one_byte_from_offset_zero():
    prim, fake = windows_lock()
    with tempfile.TemporaryFile() as fh:
        fh.write(b"xyz")        # a handle that has moved off offset 0
        prim.acquire(fh.fileno())
        prim.release(fh.fileno())
    assert fake.calls == [(FakeMsvcrt.LK_NBLCK, 0, 1),
                          (FakeMsvcrt.LK_UNLCK, 0, 1)]


def test_the_windows_primitive_reports_contention_as_blocking():
    prim, _ = windows_lock()
    with tempfile.NamedTemporaryFile() as a, open(a.name, "rb") as b:
        prim.acquire(a.fileno())
        with pytest.raises(BlockingIOError):
            prim.acquire(b.fileno())


def test_the_windows_primitive_does_not_call_other_errors_contention(
        tmp_path):
    class Broken(FakeMsvcrt):
        def locking(self, fd, mode, nbytes):
            raise OSError(errno.EBADF, "bad handle")

    prim = jsonl._msvcrt_primitive(Broken())
    with open(tmp_path / "f", "a+") as fh:
        with pytest.raises(OSError) as err:
            prim.acquire(fh.fileno())
        assert not isinstance(err.value, BlockingIOError)


# ---- dir_lock ---------------------------------------------------------------


def test_dir_lock_is_held_and_excludes_a_second_opener(tmp_path, primitive):
    with jsonl.dir_lock(tmp_path) as held:
        assert held is True
        assert _probe_is_excluded(tmp_path / jsonl.LOCK_NAME)
    assert not _probe_is_excluded(tmp_path / jsonl.LOCK_NAME)


def test_a_contended_dir_lock_fails_open(tmp_path, primitive, fast):
    with jsonl.dir_lock(tmp_path) as first:
        assert first is True
        with jsonl.dir_lock(tmp_path) as second:
            assert second is False


def test_a_missing_directory_is_not_held(tmp_path, primitive, fast):
    with jsonl.dir_lock(tmp_path / "nope") as held:
        assert held is False


def test_a_failed_unlock_exits_cleanly(tmp_path, primitive, monkeypatch):
    real = jsonl._lock

    class Failing:
        pins_files = real.pins_files
        acquire = staticmethod(real.acquire)

        @staticmethod
        def release(fd):
            raise OSError("unlock failed")

    monkeypatch.setattr(jsonl, "_lock", Failing)
    with jsonl.dir_lock(tmp_path) as held:
        assert held is True


# ---- the heal lock goes through the same primitive --------------------------


def test_the_heal_lock_is_exclusive(primitive):
    with heal.try_lock() as first:
        assert first is True
        with heal.try_lock() as second:
            assert second is False
    with heal.try_lock() as again:
        assert again is True


def test_the_heal_lock_fails_open_without_a_primitive(monkeypatch):
    monkeypatch.setattr(jsonl, "_lock", None)
    with heal.try_lock() as a, heal.try_lock() as b:
        assert a is True and b is True


def test_the_heal_lock_has_no_platform_module_of_its_own():
    assert not hasattr(heal, "_fcntl")


# ---- giving the sidecar up before the directory is removed ------------------
#
# A bucket fold unlinks the sidecar and renames the directory while it holds
# the lock. A POSIX lock lives on the inode, so that is fine. Windows will not
# unlink or rename a file another handle has open, so the holder lets go of
# the handle first (`release_for_removal`) and the removal can proceed.


def test_giving_up_a_held_sidecar_frees_it_where_the_platform_needs_that(
        tmp_path, primitive):
    sidecar = tmp_path / jsonl.LOCK_NAME
    with jsonl.dir_lock(tmp_path) as held:
        assert held is True
        jsonl.release_for_removal(tmp_path)
        if primitive.pins_files:
            assert not _probe_is_excluded(sidecar)
        else:
            assert _probe_is_excluded(sidecar)
    # leaving the block after giving the lock up is clean
    with jsonl.dir_lock(tmp_path) as again:
        assert again is True


def test_the_directory_can_be_removed_once_the_sidecar_is_given_up(
        tmp_path, primitive):
    """Real on Windows, where an open handle refuses the unlink and the
    rename; on POSIX it pins that giving up never breaks the removal."""
    d = tmp_path / "bucket"
    d.mkdir()
    with jsonl.dir_lock(d) as held:
        assert held is True
        jsonl.release_for_removal(d)
        (d / jsonl.LOCK_NAME).unlink()
        d.rmdir()
    assert not d.exists()


def test_giving_up_a_directory_nobody_holds_is_a_no_op(tmp_path, primitive):
    jsonl.release_for_removal(tmp_path)
    jsonl.release_for_removal(tmp_path / "missing")
