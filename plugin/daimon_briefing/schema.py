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
"""

import re
from typing import NamedTuple


class ItemField(NamedTuple):
    """One item-bearing checkpoint field, with the facts each consumer needs."""

    section: str    # top-level checkpoint key (working_context / epistemic_snapshot)
    key: str        # field name under the section
    singleton: bool  # True: one item dict (active_topic); False: list of item dicts
    kind: str       # recall index kind — the singular per-item label
    scoring_type: str | None  # scoring.TYPE_RULES key; None -> consumers fall
    #                           back to their own default (recall's .get)
    carries: bool   # carry.merge folds unresolved items forward (#33 Phase 2)


# Order is load-bearing: consumers iterate this tuple directly, so it feeds
# ordering-sensitive paths (iter_items walks, recall indexing, carry).
#
# carries: beliefs regenerate cheaply and active_topic is per-session by
# definition — neither carries (v1). contradictions_flagged has no dedicated
# scoring rules and never carries; its item shape varies (may be bare strings),
# which every consumer already tolerates per item.
ITEM_FIELDS: tuple[ItemField, ...] = (
    ItemField("working_context", "active_topic", True, "topic", "active_topic", False),
    ItemField("working_context", "open_questions", False, "question", "open_question", True),
    ItemField("working_context", "recent_decisions", False, "decision", "recent_decision", True),
    ItemField("epistemic_snapshot", "strong_beliefs", False, "belief", "strong_belief", False),
    ItemField("epistemic_snapshot", "uncertainties", False, "uncertainty", "uncertainty", True),
    ItemField("epistemic_snapshot", "contradictions_flagged", False, "contradiction", None, False),
)

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
CARRIED_KINDS: tuple[tuple[str, str, str], ...] = tuple(
    (f.section, f.key, f.scoring_type) for f in ITEM_FIELDS if f.carries)

# recall index kind -> scoring.TYPE_RULES key (#78 composition). Kinds without
# dedicated rules (contradiction) are absent; lookups .get their own default.
KIND_TO_TYPE: dict[str, str] = {
    f.kind: f.scoring_type for f in ITEM_FIELDS if f.scoring_type}

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
