"""Test hooks for the interleavings a lock race needs (#1411), and a stand-in
for the Windows lock module (#1401).

Everything here goes through `jsonl`'s platform seam (`jsonl._lock`), never a
platform module, so the same contract runs on POSIX and on Windows.
"""

import errno
import os

from daimon_briefing import jsonl


class FirstLockHook:
    """Stands in for `jsonl._lock`: runs `before` once, right before the
    first acquire, i.e. after the waiter opened the sidecar and before it is
    granted the lock. That is the window a fold or a rebirth uses."""

    def __init__(self, before, real=None):
        self._before = before
        self._fired = False
        self._real = real or jsonl._lock

    @property
    def pins_files(self):
        return self._real.pins_files

    def acquire(self, fd):
        if not self._fired:
            self._fired = True
            self._before()
        return self._real.acquire(fd)

    def release(self, fd):
        return self._real.release(fd)


class FakeMsvcrt:
    """The part of `msvcrt` the lock uses, with Windows' semantics: a lock is
    a BYTE RANGE starting at the handle's current position, a second handle
    to the same file cannot take an overlapping range (OSError, EACCES), and
    a lock dies with its handle."""

    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = 0, 1, 2, 3, 4

    def __init__(self):
        self.calls = []
        self._held = {}   # (file key, start, length) -> fd

    @staticmethod
    def _key(fd):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    def _live(self, rng):
        fd = self._held.get(rng)
        if fd is None:
            return None
        try:
            if self._key(fd) == rng[0]:
                return fd
        except OSError:
            pass
        del self._held[rng]   # its handle is gone: so is its lock
        return None

    def locking(self, fd, mode, nbytes):
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        self.calls.append((mode, pos, nbytes))
        rng = (self._key(fd), pos, nbytes)
        owner = self._live(rng)
        if mode == self.LK_UNLCK:
            if owner != fd:
                raise OSError(errno.EACCES, "not locked by this handle")
            del self._held[rng]
            return
        if owner is not None and owner != fd:
            raise OSError(errno.EACCES, "locked by another handle")
        self._held[rng] = fd


def windows_lock():
    """(primitive, fake msvcrt): `jsonl`'s Windows primitive over the fake."""
    fake = FakeMsvcrt()
    return jsonl._msvcrt_primitive(fake), fake
