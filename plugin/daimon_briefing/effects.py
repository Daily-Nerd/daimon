"""What a read decided to write afterwards, as data (#1132 PR 6a).

A read verb used to write as it went: usage lines, telemetry rows, the seen
state, a delivered mark. `Effects` is the record of those intentions, built
by pure code and committed once after the output is flushed. This module ships
the type and its algebra only; `effects_commit` (an entry module) performs the
writes.

Stdlib only, no imports from the package: it sits below everything.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal


# The record types are frozen dataclasses, like `Effects` itself: named
# fields (a producer and the committer agree by name, which mypy checks) and
# the same immutability, which a NamedTuple would not give a reader of the
# type. A mapping or list inside one is the producer's own copy.


@dataclass(frozen=True)
class Surfaced:
    """A request card the brief printed in full: stamp `surfaced` (kind
    "request") or `verdict_surfaced` (kind "verdict", with the late reply it
    showed) in `project`'s own bucket."""
    kind: Literal["request", "verdict"]
    project: str
    request_id: str
    reply_event_id: str | None = None


@dataclass(frozen=True)
class Verification:
    """The worldcheck bookkeeping of one brief: `stats` (counters), `rows`
    (ledger rows for `route`), `project` (the receipt-probe usage scope)."""
    project: str
    route: str
    stats: dict
    rows: tuple


@dataclass(frozen=True)
class Telemetry:
    """One recall-delivery record: the delivered `rows` and the keyword
    arguments `recall_telemetry.record` takes."""
    rows: list
    kwargs: dict


@dataclass(frozen=True)
class Effects:
    """Every field is a tuple of records the committer understands; the
    fields with a producer are typed by record, the rest stay opaque until
    their first producer lands. `Effects()` is the empty value; fields only
    ever grow by `merge`."""

    surfaced: tuple[Surfaced, ...] = ()
    delivered: tuple = ()
    verdict_delivered: tuple = ()
    verification: tuple[Verification, ...] = ()
    usage: tuple[str, ...] = ()
    telemetry: tuple[Telemetry, ...] = ()
    seen: tuple = ()
    error_log: tuple = ()

    @classmethod
    def none(cls) -> "Effects":
        return cls()


def merge(a: Effects, b: Effects) -> Effects:
    """`a` then `b`, field by field. Associative, with `Effects.none()` as the
    identity."""
    return Effects(**{f.name: getattr(a, f.name) + getattr(b, f.name)
                      for f in fields(Effects)})
