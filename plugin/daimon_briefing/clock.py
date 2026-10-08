"""The one place a ledger row reads the time and mints an id.

Every ledger stamp takes its `order` (nanoseconds since the epoch) and its
`event_id` from here. With no clock active, `now_ns()` is `time.time_ns()` and
`new_id()` is `uuid.uuid4().hex`, both read at call time, so a test that
patches `time.time_ns` still sees its patch. Under `use(clock)` the active
clock answers instead, which is how a host test gets reproducible rows.

Requests and rulings share one clock domain (their `order` values are compared
across the two ledgers), so both draw from this module and nothing else.

The active clock lives in a ContextVar. It propagates through
`asyncio.to_thread` (the context is copied) and into tasks created while it is
active. It does NOT propagate into `threading.Thread` or
`loop.run_in_executor`, which start with their own context: a clock set in the
caller is invisible there and the wall clock answers.
"""

import hashlib
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Protocol, runtime_checkable

# open_request refuses an id collision "opened in the same second", so a
# deterministic clock must advance at least this far per reading.
MIN_STEP_NS = 1_000_000_000


@runtime_checkable
class Clock(Protocol):
    def now_ns(self) -> int: ...

    def new_id(self) -> str: ...


_ACTIVE: ContextVar[Clock | None] = ContextVar("daimon_clock", default=None)


def now_ns() -> int:
    active = _ACTIVE.get()
    return time.time_ns() if active is None else int(active.now_ns())


def new_id() -> str:
    active = _ACTIVE.get()
    return uuid.uuid4().hex if active is None else active.new_id()


def stamp_identity(now_ns_: int | None = None,
                   event_id: str | None = None) -> tuple[int, str]:
    """(order, event_id) for a ledger row.

    An explicit argument wins over the active clock, which wins over the wall
    clock. A falsy `event_id` is treated as absent, as every ledger always did.
    """
    order = now_ns() if now_ns_ is None else int(now_ns_)
    return order, event_id or new_id()


@contextmanager
def use(clock: Clock) -> Iterator[None]:
    """Make `clock` answer `now_ns()` and `new_id()` in the calling context."""
    token = _ACTIVE.set(clock)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


class StepClock:
    """A clock that counts: reading k returns `start_ns + k * step_ns`.

    Ids are `sha256(f"{seed}:{n}")` truncated to 32 hex characters, with their
    own counter. Two fresh instances with the same arguments produce the same
    sequence. Thread-safe. A step under one second is refused: two asks opened
    inside one second collide on their request id.
    """

    def __init__(self, start_ns: int, step_ns: int = MIN_STEP_NS,
                 seed: str = "daimon") -> None:
        if step_ns < MIN_STEP_NS:
            raise ValueError(
                f"step_ns must be at least {MIN_STEP_NS} (one second): "
                "request ids hash the second")
        self._start = int(start_ns)
        self._step = int(step_ns)
        self._seed = seed
        self._ticks = 0
        self._ids = 0
        self._lock = threading.Lock()

    def now_ns(self) -> int:
        with self._lock:
            value = self._start + self._ticks * self._step
            self._ticks += 1
        return value

    def new_id(self) -> str:
        with self._lock:
            n = self._ids
            self._ids += 1
        return hashlib.sha256(f"{self._seed}:{n}".encode()).hexdigest()[:32]
