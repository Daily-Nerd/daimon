"""Supported test helpers for hosts that embed daimon.

This is a versioned, public surface for a host's own test harness (golden
snapshots, reproducible ledgers). `api` stays read-only and exports nothing
that writes; the way to make writes reproducible lives here instead.

    from daimon_briefing import testing

    with testing.deterministic(testing.StepClock(1_700_000_000 * 10**9)):
        ...  # open_request, rulings, amendments: same order, ts, ids every run

The clock is carried in a ContextVar: it follows `asyncio.to_thread` but not
`threading.Thread` or `loop.run_in_executor` (see `daimon_briefing.clock`).
"""

from .clock import Clock, StepClock, use as deterministic

__all__ = ["Clock", "StepClock", "deterministic"]
