"""Test hooks for the interleavings a lock race needs (#1411), and a stand-in
for the Windows lock module (#1401).

Everything here goes through `jsonl`'s platform seam (`jsonl._lock`), never a
platform module, so the same contract runs on POSIX and on Windows.
"""

import errno
import gc
import io
import os
import weakref

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
        self._held = {}   # (file key, start, length) -> (fd, open file | None)

    @staticmethod
    def _key(fd):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    @staticmethod
    def _open_file(fd):
        """The open file object behind `fd` (a weak reference), so a lock is
        tied to THAT handle and not to a number the OS may hand out again."""
        for obj in gc.get_objects():
            if isinstance(obj, io.FileIO) and not obj.closed \
                    and obj.fileno() == fd:
                return weakref.ref(obj)
        return None

    def _live(self, rng):
        """The fd that holds `rng`, or None when its handle is gone."""
        held = self._held.get(rng)
        if held is None:
            return None
        fd, ref = held
        if ref is not None:
            obj = ref()
            if obj is not None and not obj.closed:
                return fd
        else:
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
        self._held[rng] = (fd, self._open_file(fd))


def windows_lock():
    """(primitive, fake msvcrt): `jsonl`'s Windows primitive over the fake."""
    fake = FakeMsvcrt()
    return jsonl._msvcrt_primitive(fake), fake
