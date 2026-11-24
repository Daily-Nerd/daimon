"""Test hooks for the interleavings a lock race needs (#1411)."""

import fcntl


class FirstFlockHook:
    """Stands in for `jsonl._fcntl`: runs `before` once, right before the
    first flock, i.e. after the waiter opened the sidecar and before it is
    granted the lock. That is the window a fold or a rebirth uses."""

    LOCK_EX = fcntl.LOCK_EX
    LOCK_NB = fcntl.LOCK_NB
    LOCK_UN = fcntl.LOCK_UN

    def __init__(self, before):
        self._before = before
        self._fired = False

    def flock(self, fd, op):
        if not self._fired:
            self._fired = True
            self._before()
        return fcntl.flock(fd, op)
