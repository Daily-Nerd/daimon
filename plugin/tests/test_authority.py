"""#1301: one owner answers "what authority tier does this row carry?".

`authority.of_row(ledger, row)` returns exactly what the per-ledger channel
tables answered before it existed (pinned by `test_authority_golden.py`);
these tests pin the contract of the module itself.
"""
import pytest

from daimon_briefing import authority

LEDGERS = ("refutations", "requests", "amendments", "trust", "relations")


@pytest.mark.parametrize("ledger", LEDGERS)
@pytest.mark.parametrize("channel,tier", [
    ("cli-agent", "agent"), ("cli-tty", "human"), ("ui", "human"),
    ("signed", "human"), ("nonsense", None), ("", None),
])
def test_the_shared_four_carry_the_same_tier_in_every_ledger(
        ledger, channel, tier):
    assert authority.of_row(ledger, {"channel": channel}) == tier
    assert authority.of_channel(ledger, channel) == tier


def test_each_ledger_keeps_the_extra_channels_it_always_had():
    assert authority.of_channel("refutations", "mechanical") == "mechanical"
    assert authority.of_channel("requests", "mechanical") == "mechanical"
    assert authority.of_channel("amendments", "mechanical") == "mechanical"
    assert authority.of_channel("relations", "serializer") == "agent"
    assert authority.of_channel("relations", "lab-import") == "agent"
    # a channel another ledger owns is unknown here, not borrowed
    assert authority.of_channel("trust", "mechanical") is None
    assert authority.of_channel("trust", "serializer") is None
    assert authority.of_channel("relations", "mechanical") is None
    assert authority.of_channel("refutations", "serializer") is None


@pytest.mark.parametrize("row", [{}, {"channel": None}, {"channel": 5},
                                 {"channel": ["cli-tty"]}])
def test_an_unusable_channel_carries_no_tier(row):
    assert authority.of_row("refutations", row) is None


def test_the_stored_authority_field_is_never_the_answer():
    row = {"channel": "cli-agent", "authority": "human"}
    assert authority.of_row("requests", row) == "agent"
    assert authority.of_row("trust", {"authority": "human"}) is None


def test_the_events_ledger_reads_its_source_field():
    for source in ("cli", "cli-tty", "ui"):
        assert authority.of_row("events", {"source": source}) == "human"
    for source in ("agent", "serializer"):
        assert authority.of_row("events", {"source": source}) == "agent"
    for row in ({}, {"source": ""}, {"source": "weird"}, {"source": None}):
        assert authority.of_row("events", row) is None
    assert authority.of_row("events", {"channel": "cli-tty"}) is None


def test_an_unknown_ledger_is_loud():
    with pytest.raises(KeyError):
        authority.of_row("nope", {"channel": "cli-tty"})


def test_the_known_channels_are_the_table_keys():
    assert authority.channels("trust") == frozenset(
        {"cli-agent", "cli-tty", "ui", "signed"})
    assert authority.channels("relations") >= {"serializer", "lab-import"}
    assert authority.channels("refutations") == frozenset(
        authority.table("refutations"))
