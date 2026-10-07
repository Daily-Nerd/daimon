"""`effects.Effects`: the frozen record of what a read decided to write
afterwards (#1132 PR 6a); `effects_commit` writes it."""

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


def test_the_record_types_are_frozen_and_named():
    s = effects.Surfaced("request", "/p", "q-1", None)
    assert (s.kind, s.project, s.request_id, s.reply_event_id) == (
        "request", "/p", "q-1", None)
    v = effects.Verification("/p", "/p", {"fired": 1}, ())
    assert (v.project, v.route, v.stats, v.rows) == ("/p", "/p",
                                                      {"fired": 1}, ())
    t = effects.Telemetry([], {"via": "mcp"})
    assert (t.rows, t.kwargs) == ([], {"via": "mcp"})
    for record in (s, v, t):
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.rows = 1  # type: ignore[attr-defined]


def test_the_fields_a_producer_exists_for_are_typed_by_record():
    hints = {f.name: f.type for f in dataclasses.fields(effects.Effects)}
    assert hints["surfaced"] == "tuple[Surfaced, ...]"
    assert hints["verification"] == "tuple[Verification, ...]"
    assert hints["telemetry"] == "tuple[Telemetry, ...]"
    assert hints["usage"] == "tuple[str, ...]"
