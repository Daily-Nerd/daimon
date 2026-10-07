"""`effects.Effects`: the frozen record of what a read decided to write
afterwards (#1132 PR 6a, type only: nothing commits it yet)."""

import dataclasses

import pytest

from daimon_briefing import effects

FIELDS = ("surfaced", "delivered", "verdict_delivered", "verification",
          "usage", "telemetry", "seen", "error_log")


def test_effects_has_exactly_the_declared_fields_defaulting_to_empty():
    assert tuple(f.name for f in dataclasses.fields(effects.Effects)) == FIELDS
    assert all(getattr(effects.Effects(), name) == () for name in FIELDS)


def test_effects_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        effects.Effects().usage = ("x",)  # type: ignore[misc]


def test_none_is_the_empty_effects():
    assert effects.Effects.none() == effects.Effects()


def test_merge_concatenates_each_field_in_order():
    a = effects.Effects(usage=("a",), seen=("s1",))
    b = effects.Effects(usage=("b",), telemetry=("t",))
    got = effects.merge(a, b)
    assert got.usage == ("a", "b")
    assert got.seen == ("s1",) and got.telemetry == ("t",)
    assert got.surfaced == ()


def test_merge_with_none_is_the_identity_and_is_associative():
    a = effects.Effects(usage=("a",))
    b = effects.Effects(usage=("b",), error_log=("e",))
    c = effects.Effects(seen=("s",))
    assert effects.merge(a, effects.Effects.none()) == a
    assert effects.merge(effects.merge(a, b), c) == effects.merge(
        a, effects.merge(b, c))
