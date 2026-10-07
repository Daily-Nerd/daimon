"""Checkpoint item-field schema — the single source of truth (#146).

store, serializer, recall, and carry each hand-maintained their own copy of
"the item-bearing fields of a checkpoint", and the copies drifted:
serializer.iter_items omitted contradictions_flagged, so those items skipped
first_seen stamping and importance sanitization while still receiving ids,
redaction, recall indexing, and withhold treatment. Every consumer now derives
its view from ITEM_FIELDS below; a field added here propagates to all of them
(and to the serialize→brief E2E test, which iterates this table).

Deliberately import-free: store imports serializer, recall imports store,
carry imports recall — this module sits below the whole chain so any of them
can import it without a cycle. compare_format_versions lives here for the same
reason: cli's status check and render's brief note (#294) both need it, and
neither one imports the other.

Admission-state doctrine (#1109): no field on ItemField below carries a
per-item candidate, verified, stale, or rejected state, by decision, not
omission. Recall already ranks a stale, superseded, or contradicted item
down instead of filtering it — an old or contradicted decision is still
evidence a reader may need, so a machine signal, however confident, only
ever ranks down (a candidate signal never suppresses an item). A closed
state machine that can WITHHOLD a checkpoint item's text exists in exactly
one place: a separate, value-keyed ledger (`trust.py`, mirroring the ruling
half of `refutations.py`) that a human channel alone may confirm or
release, keyed on the item's TEXT rather than its id so a carried or
re-extracted copy of the same value stays withheld. The distinction that
matters is not "field vs. ledger" but WHO can move the state: a
human-confirmed verdict may withhold; a machine signal only ever ranks
down.
"""

import re
from collections.abc import Iterator
from typing import Any, NamedTuple


class ItemField(NamedTuple):
    """One item-bearing checkpoint field, with the facts each consumer needs."""

    section: str    # top-level checkpoint key (working_context / epistemic_snapshot)
    key: str        # field name under the section
    singleton: bool  # True: one item dict (active_topic); False: list of item dicts
    kind: str       # recall index kind — the singular per-item label
    scoring_type: str | None  # scoring.TYPE_RULES key; None -> consumers fall
    #                           back to their own default (recall's .get)
    carries: bool   # carry.merge folds unresolved items forward (#33 Phase 2)
    briefable: bool  # loop-shaped: the briefing stamps a ` [id]` handle on it and
    #                  `daimon loops` / `daimon amend` address it (#480 scope rule)


# Order is load-bearing: consumers iterate this tuple directly, so it feeds
# ordering-sensitive paths (iter_items walks, recall indexing, carry).
#
# carries: beliefs regenerate cheaply and active_topic is per-session by
# definition — neither carries (v1). contradictions_flagged has no dedicated
# scoring rules and never carries; its item shape varies (may be bare strings),
# which every consumer already tolerates per item.
#
# briefable: only the loop-shaped fields. Decisions, beliefs and contradictions
# are valid `daimon resolve` targets too, but stamping a handle there would
# invite resolving settled facts.
ITEM_FIELDS: tuple[ItemField, ...] = (
    ItemField("working_context", "active_topic", True, "topic", "active_topic", False, False),
    ItemField("working_context", "open_questions", False, "question", "open_question", True, True),
    ItemField("working_context", "recent_decisions", False, "decision", "recent_decision", True, False),
    ItemField("epistemic_snapshot", "strong_beliefs", False, "belief", "strong_belief", False, False),
    ItemField("epistemic_snapshot", "uncertainties", False, "uncertainty", "uncertainty", True, True),
    ItemField("epistemic_snapshot", "contradictions_flagged", False, "contradiction", None, False, False),
)

# The item columns that hold content a reader could be shown: the ones a
# withhold decision matches by canonical value (#1132). A column here is
# checked wherever a value can surface; one that is not is never inspected.
VALUE_FIELDS: tuple[str, ...] = ("text", "quote", "scene")

# (section, key) for the list sections that hold checkpoint items — store's
# redaction/id-stamping view. active_topic is a single per-session dict and
# never needs an id (it does not carry, #33).
ITEM_LISTS: tuple[tuple[str, str], ...] = tuple(
    (f.section, f.key) for f in ITEM_FIELDS if not f.singleton)

# (section, key, indexed kind) — recall's index view: every trust-tagged
# cognitive field, active_topic included.
KIND_SOURCES: tuple[tuple[str, str, str], ...] = tuple(
    (f.section, f.key, f.kind) for f in ITEM_FIELDS)

# (section, key, scoring TYPE_RULES type) — carry's view: carried fields only.
#
# Filters on scoring_type as well as carries (#842). Every carried field has
# one today and the field table is the single source that decides, but the
# filter previously trusted an invariant nothing enforced: a carried field
# added without a scoring type would have put None into a tuple typed str,
# and carry would have used it as a TYPE_RULES key and silently fallen back
# to the default rules. The invariant is now pinned by test, so this filter
# can never quietly DROP a field either.
CARRIED_KINDS: tuple[tuple[str, str, str], ...] = tuple(
    (f.section, f.key, f.scoring_type) for f in ITEM_FIELDS
    if f.carries and f.scoring_type)

# recall index kind -> scoring.TYPE_RULES key (#78 composition). Kinds without
# dedicated rules (contradiction) are absent; lookups .get their own default.
KIND_TO_TYPE: dict[str, str] = {
    f.kind: f.scoring_type for f in ITEM_FIELDS if f.scoring_type}

def iter_fields(checkpoint) -> Iterator[tuple[ItemField, Any]]:
    """Yield `(field, value)` for every field of ITEM_FIELDS whose block is
    present, in table order. `value` is the raw thing stored under the key
    (a list, a dict, None, or anything a torn checkpoint holds): for the
    loops that rebuild a list rather than visit its items. A checkpoint
    that is not a dict, or a block that is not a dict, yields nothing for
    that block instead of raising."""
    if not isinstance(checkpoint, dict):
        return
    for field in ITEM_FIELDS:
        block = checkpoint.get(field.section)
        if isinstance(block, dict):
            yield field, block.get(field.key)


def iter_items(checkpoint, *, dicts_only: bool = True
               ) -> Iterator[tuple[ItemField, Any]]:
    """Yield `(field, item)` for every item a checkpoint holds, in
    ITEM_FIELDS order: the singleton (active_topic) when it is a dict, then
    each entry of each list field. The one walker for cross-cutting
    per-item passes (#126, #146): store's first_seen stamping,
    sanitize_importance, anchor's drift scan.

    Tolerant of an absent or non-dict block, and of a list field that holds
    something other than a list (torn or legacy checkpoints). `dicts_only`
    (default) skips list entries that are not dicts, which is what the
    sanitizers want; `dicts_only=False` yields them as they are, for a
    reader that tolerates them per item (contradictions_flagged may hold
    bare strings, and anchor.drifted skips them itself). Items come back by
    reference, so a caller may mutate them in place."""
    for field, value in iter_fields(checkpoint):
        if field.singleton:
            if isinstance(value, dict):
                yield field, value
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            if dicts_only and not isinstance(item, dict):
                continue
            yield field, item


_FORMAT_VERSION_RE = re.compile(r"D-(\d+)")


def compare_format_versions(a: str, b: str) -> int | None:
    """Order-compare two PROMPT_VERSION-shaped strings ("D-NNN") by their integer
    suffix — a plain string compare gets multi-digit versions wrong (#294:
    "D-9" > "D-10" lexically, backwards). Returns a positive int if `a` is newer
    than `b`, negative if older, 0 if equal, or None if either side isn't a
    parseable D-NNN — callers fail soft on None rather than raising."""
    ma = _FORMAT_VERSION_RE.fullmatch(a) if isinstance(a, str) else None
    mb = _FORMAT_VERSION_RE.fullmatch(b) if isinstance(b, str) else None
    if ma is None or mb is None:
        return None
    return int(ma.group(1)) - int(mb.group(1))
