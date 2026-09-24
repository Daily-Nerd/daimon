"""Shared write-channel authority model for append-only ledgers (#1109 Slice 0).

`CHANNEL_AUTHORITY` answers one question for every ledger built on this
doctrine: which tier of authority (`agent`, `human`, `mechanical`) does an
OBSERVED write channel carry? Authority is a property of the write path,
never a caller's claim about itself: `--by human` was a flag whose only
function was letting the caller assert its own authority, the echo-defense
hole (#512) and the self-assigned-identity hole (scar 0032) one layer up —
an actor acting as witness for its own claim. `ui` and `signed` are reachable
only from an in-process writer, which is what forces a future UI to be a
WRITER rather than a wrapper around the deleted flag: if the CLI could emit
`ui`, an agent shelling out could too.

Before #1109, `refutations.py` and `relations.py` each carried an
independent copy of this table, and the copies had drifted: `relations.py`
added `serializer`/`lab-import` (its own two import paths, both agent-tier)
without `mechanical`; `refutations.py` added `mechanical` (#581's future
automatic activation) without the two importer channels. Both copies still
agreed on the shared four (`cli-agent`, `cli-tty`, `ui`, `signed`), so this
module keeps that agreement as `BASE_CHANNEL_AUTHORITY` and lets each ledger
layer its own extra channels on top via `merged_authority`, rather than
either widening every ledger to every channel or re-forking a third copy for
the next one (#1109's own ledger, `trust.py`, needs none of the extras and
imports the base unchanged).

`CHANNEL_LABEL` was never duplicated — only `refutations.py` ever rendered
it; `relations.py`'s own render reads its folded `confirmed_channel`/
`confirmed_author` fields directly rather than a label map — so it moves
here unchanged, still reachable at `refutations.CHANNEL_LABEL` for existing
importers (`amendments.py`, `requests.py`).
"""

from __future__ import annotations

# The four channel tiers every ledger in this doctrine shares. A module that
# needs an additional channel (`refutations.py`'s `mechanical`,
# `relations.py`'s `serializer`/`lab-import`) layers it on top with
# `merged_authority` rather than editing this dict — a channel added here
# would become available to every ledger at once, which is not what either
# module's own history asked for.
BASE_CHANNEL_AUTHORITY: dict[str, str] = {
    "cli-agent": "agent",
    "cli-tty": "human",
    "ui": "human",
    "signed": "human",
}

# What each channel is allowed to say about itself when rendered. Never
# "human-ratified" unqualified: the tier is the honest part. `mechanical` is
# listed even though it is not in `BASE_CHANNEL_AUTHORITY` — it is
# `refutations.py`'s own override channel, and its label belongs with the
# rest rather than forking a second map.
CHANNEL_LABEL: dict[str, str] = {
    "cli-agent": "agent-proposed",
    "cli-tty": "ratified (interactive)",
    "ui": "ratified (ui)",
    "signed": "ratified (signed)",
    "mechanical": "mechanically-activated",
}


def merged_authority(*overrides: dict) -> dict[str, str]:
    """`BASE_CHANNEL_AUTHORITY` plus every override dict, later wins.

    Returns a fresh dict on every call — a ledger module binds the result to
    its own module-level `CHANNEL_AUTHORITY` name at import time, so no two
    modules ever share the same mutable object and one module's future
    channel addition can never leak into another's."""
    out = dict(BASE_CHANNEL_AUTHORITY)
    for override in overrides:
        out.update(override)
    return out
