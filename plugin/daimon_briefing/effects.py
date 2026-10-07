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


@dataclass(frozen=True)
class Effects:
    """Every field is a tuple of opaque records the committer understands.
    `Effects()` is the empty value; fields only ever grow by `merge`."""

    surfaced: tuple = ()
    delivered: tuple = ()
    verdict_delivered: tuple = ()
    verification: tuple = ()
    usage: tuple = ()
    telemetry: tuple = ()
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
