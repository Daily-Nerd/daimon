"""The one owner of "what authority tier does this row carry?" (#1301).

Whether a ledger row is a human act, an agent act or a mechanical one used to
be decided in every ledger module, each with its own lookup into its own copy
of a channel table, and in the events ledger through a separate set of
`source` values. A copy that was missed answered differently from the rest.

`of_row(ledger, row)` is the single answer. In this change it returns exactly
what those lookups returned: the row's CHANNEL (its `source` for the events
ledger) mapped through that ledger's table, and `None` for a channel the
ledger does not know. It never reads the row's stored `authority` field: that
is the row's claim about itself, and authority is a property of the observed
write path (see `channels.py`).

The tables live here. The ledger modules keep their `CHANNEL_AUTHORITY` and
`CHANNELS` names only as bindings to the same objects, for importers outside
the repo that pin them; nothing in the package reads through those names, and
`tests/test_authority_census.py` fails the build when something does.

A later change that alters what counts as human alters it in this file only.
"""

from __future__ import annotations

from . import channels as _channels

# Event-ledger `source` values whose tier is HUMAN. `ui` and `cli-tty` are
# peers of `cli`, not replacements: the ledger is append-only and every row
# already written says `cli`, so the set widens and never renames. `cli` is
# the legacy human source. Widening it widens WHO counts, never WHAT counts:
# callers must still filter on `kind` themselves (scar 0025 - kind never
# isolates a fold on its own).
HUMAN_EVENT_SOURCES = frozenset({"cli", "cli-tty", "ui"})
# Sources a machine path names itself with. They are agent tier; which of the
# two a row carries (`serializer` verified a claim, `agent` made one) is a
# credit category the stats fold reads off the source, not a tier.
AGENT_EVENT_SOURCES = frozenset({"agent", "serializer"})

# The rulings table is one object shared by the three ledgers built on it
# (rulings and refutations, requests, amendments): a channel any of them
# admits, all of them admit.
_RULING_TABLE = _channels.merged_authority({"mechanical": "mechanical"})

_TABLES: dict[str, dict[str, str]] = {
    "refutations": _RULING_TABLE,
    "requests": _RULING_TABLE,
    "amendments": _RULING_TABLE,
    # Only the shared four: an agent proposes, a human decides. No
    # `mechanical` tier and no importer channels.
    "trust": _channels.merged_authority(),
    # `serializer`/`lab-import` are relations' own two importer channels.
    "relations": _channels.merged_authority(
        {"serializer": "agent", "lab-import": "agent"}),
    "events": {
        **{source: "human" for source in HUMAN_EVENT_SOURCES},
        **{source: "agent" for source in AGENT_EVENT_SOURCES},
    },
}

# The row field that names the write path, per ledger.
_FIELD = {"events": "source"}


def table(ledger: str) -> dict[str, str]:
    """The ledger's channel -> tier mapping, the live object. For the ledger
    modules' re-export of their historical constant; read tiers through
    `of_channel` / `of_row`."""
    return _TABLES[ledger]


def channels(ledger: str) -> frozenset[str]:
    """The channels `ledger` admits."""
    return frozenset(_TABLES[ledger])


def of_channel(ledger: str, channel) -> str | None:
    """The tier `channel` carries in `ledger`, or None for one it does not
    know. For callers that hold the channel of an act rather than a row (a
    write boundary, a gate). Anything but a string is not a channel."""
    return _TABLES[ledger].get(str(channel or ""))


def of_row(ledger: str, row: dict) -> str | None:
    """The authority tier of one row of `ledger`: `human`, `agent`,
    `mechanical`, or None when the row's channel is absent or unknown.

    Rows are read off disk, so the channel can be missing or not a string;
    neither table has an empty-string key, so an unusable channel misses
    exactly as it always did."""
    return of_channel(ledger, row.get(_FIELD.get(ledger, "channel")))
