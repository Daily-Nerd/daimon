"""Project-scoped typed-relation ledger (#678 Phase 2, shadow mode).

Relations describe how cognitive items relate across checkpoints — revision,
answer, supersession, arc membership — as an append-only stream of typed
events folded into current records.  Candidates are behaviorally inert:
recall, lifecycle, corroboration, and carry read nothing here, and the only
reader-facing surface is the viewer's History lane, which renders
CONFIRMED records alone (`for_item`) — a chain a reader sees is always one
a human vouched for.

Every writable string is either a hash-derived id or drawn from a closed set,
refused at the seam otherwise.  That gate is load-bearing: rows referencing
forgotten items survive on disk (fork A ratifies that forget REACHES this
file — see `forget_item_id`), so no field may ever be able to carry item text.

Authority is a property of the write path, never a caller's claim (the
refutation ledger's contract).  Agents and the lab import can only propose;
state moves on human channels alone, and there is no mechanical channel in
v1 because Phase 1 proved no evidence rail qualifies for automatic
confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from . import config, policy, provenance, schema, store


VERSION = 1
EVENTS = frozenset({"proposed", "confirmed", "rejected", "retracted"})
STATES = frozenset({"candidate", "confirmed", "rejected", "retracted"})
TYPES = frozenset({
    "revision-of", "answers", "supersedes", "reclassified-from",
    "quoted-from", "same-arc",
})
# Symmetric types name one fact regardless of endpoint order, so their two
# spellings must mint ONE id (endpoints sort before hashing) — otherwise a
# single arc becomes two records and every arc metric double-counts.
SYMMETRIC_TYPES = frozenset({"same-arc"})
# Persisted rails are a closed set: `matched_by` is the one list-shaped field
# a writer controls, and an open vocabulary here is a free-text channel into
# a file whose deletion story depends on no field ever carrying prose.
RAILS = frozenset({
    "exact-text", "bound-exact-quote", "shared-source-message",
    "exact-anchor", "carry-absolute", "carry-ratio", "typed-supersedes",
})
NOTE_CODES = frozenset({""})
# No `mechanical` channel, deliberately: adding machine confirmation later
# requires the held-out gate (zero identity-confirmations on arc specimens)
# and a version bump, not a vocabulary entry.
CHANNEL_AUTHORITY = {
    "serializer": "agent",
    "lab-import": "agent",
    "cli-agent": "agent",
    "cli-tty": "human",
    "ui": "human",
    "signed": "human",
}
CHANNELS = frozenset(CHANNEL_AUTHORITY)
_EVENT_RANK = {
    # Same-order ambiguity fails toward refusal: a confirm/reject tie lands
    # on rejected (rank strictly after confirmed — the refuter round proved
    # a shared rank resolves opposite verdicts by uuid4 comparison), and a
    # retraction outranks both.
    "proposed": 0,
    "confirmed": 1,
    "rejected": 2,
    "retracted": 3,
}
_REL_ID_RE = re.compile(r"rel-[0-9a-f]{16}")
# Everything policy.stamp_item_ids can mint: prefix letter per list key,
# width ladder 12/16/24/40 plus the legacy 6-hex era ({6,40} covers both),
# and the `-{n}` identical-text twin counter.
_ITEM_ID_RE = re.compile(r"[orsuc]-[0-9a-f]{6,40}(?:-\d+)?")
_MATCHER_RE = re.compile(r"[a-z0-9-]{1,32}")
_FIELD_KEYS = frozenset(key for _, key in schema.ITEM_LISTS)
_MAX_RAILS = 8
_MAX_AUTHOR = 200
# With every field capped or closed above, normal input cannot approach this;
# reaching it is a caller bug and refusal is LOUD (RelationError), never a
# silent False the audit would read as absence.
_MAX_ROW_BYTES = 2048
_FORGOTTEN_PREFIX = "forgotten:"


class RelationError(ValueError):
    """A requested ledger row or transition is invalid."""


def _path(project_dir=None):
    slug = store.project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "relations.jsonl"


def _validate_endpoint(name: str, endpoint) -> dict:
    if not isinstance(endpoint, dict):
        raise RelationError(f"{name} endpoint must be a dict")
    session_id = str(endpoint.get("session_id") or "")
    field = str(endpoint.get("field") or "")
    item_id = str(endpoint.get("item_id") or "")
    if not provenance.valid_session_id(session_id):
        raise RelationError(f"{name}.session_id is not a valid session id")
    if field not in _FIELD_KEYS:
        raise RelationError(
            f"{name}.field must be one of: {', '.join(sorted(_FIELD_KEYS))}")
    if not _ITEM_ID_RE.fullmatch(item_id):
        raise RelationError(f"{name}.item_id is not a minted item id")
    return {"session_id": session_id, "field": field, "item_id": item_id}


def make_id(type_: str, from_endpoint: dict, to_endpoint: dict) -> str:
    """Deterministic edge id.  `field` and matcher metadata are excluded:
    the id names the edge, not how it was observed or displayed."""
    if type_ not in TYPES:
        raise RelationError(f"unknown relation type: {type_}")
    a = (from_endpoint["session_id"], from_endpoint["item_id"])
    b = (to_endpoint["session_id"], to_endpoint["item_id"])
    if type_ in SYMMETRIC_TYPES and b < a:
        a, b = b, a
    raw = "\0".join((type_, a[0], a[1], b[0], b[1]))
    return "rel-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stamp(event: str, relation_id: str, channel: str,
           *, now_ns: int | None = None) -> dict:
    if event not in EVENTS:
        raise RelationError(f"unknown relation event: {event}")
    if not _REL_ID_RE.fullmatch(str(relation_id or "")):
        raise RelationError(f"invalid relation id: {relation_id!r}")
    if channel not in CHANNELS:
        raise RelationError(
            f"channel must be one of: {', '.join(sorted(CHANNELS))}")
    order = time.time_ns() if now_ns is None else int(now_ns)
    ts = datetime.fromtimestamp(order / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return {
        "version": VERSION,
        "ts": ts,
        "order": order,
        "event_id": uuid.uuid4().hex,
        "event": event,
        "relation_id": relation_id,
        "channel": channel,
        # Derived, never accepted: no way to name one channel and claim
        # another's authority.
        "authority": CHANNEL_AUTHORITY[channel],
        "author": str(config.author() or "")[:_MAX_AUTHOR],
    }


def _is_torn(path) -> bool:
    """True when the last append died before writing its terminator."""
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def _append(row: dict, project_dir=None) -> bool:
    """Append one admitted row.  Never raises; never mutates another ledger."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    admitted = policy.admit_row(row, redact_fields=("author",))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            if _is_torn(path):
                handle.write("\n")
            handle.write(json.dumps(admitted, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _write(row: dict, project_dir=None) -> None:
    blob = json.dumps(row, ensure_ascii=False)
    if len(blob.encode("utf-8")) > _MAX_ROW_BYTES:
        raise RelationError(
            f"relation row too large ({len(blob)} > {_MAX_ROW_BYTES} bytes)")
    if not _append(row, project_dir=project_dir):
        raise RelationError(
            "relation not written (daimon disabled, project unknown, or "
            "ledger unwritable)")


def propose(*, type_: str, from_endpoint, to_endpoint, matched_by,
            matcher_version: str, channel: str, note_code: str = "",
            project_dir=None, now_ns: int | None = None) -> str:
    """Record a candidate edge.  Any channel may propose; none may confirm."""
    if type_ not in TYPES:
        raise RelationError(f"unknown relation type: {type_}")
    frm = _validate_endpoint("from", from_endpoint)
    to = _validate_endpoint("to", to_endpoint)
    rails = [str(rail) for rail in (matched_by or [])]
    if not rails or len(rails) > _MAX_RAILS:
        raise RelationError(
            f"matched_by must name 1..{_MAX_RAILS} evidence rails")
    unknown = set(rails) - RAILS
    if unknown:
        raise RelationError(
            f"unknown evidence rail: {', '.join(sorted(unknown))}")
    if not _MATCHER_RE.fullmatch(str(matcher_version or "")):
        raise RelationError("matcher_version must match [a-z0-9-]{1,32}")
    if str(note_code or "") not in NOTE_CODES:
        raise RelationError(f"unknown note_code: {note_code!r}")
    if type_ in SYMMETRIC_TYPES:
        # Store what the id hashed, so the record is one canonical fact.
        a = (frm["session_id"], frm["item_id"])
        b = (to["session_id"], to["item_id"])
        if b < a:
            frm, to = to, frm
    rel_id = make_id(type_, frm, to)
    row = _stamp("proposed", rel_id, channel, now_ns=now_ns)
    row.update({
        "type": type_,
        "from": frm,
        "to": to,
        "matched_by": rails,
        "matcher_version": str(matcher_version),
        "note_code": str(note_code or ""),
    })
    _write(row, project_dir=project_dir)
    return rel_id


def _human_transition(event: str, relation_id: str, channel: str,
                      project_dir, now_ns) -> None:
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise RelationError(
            f"{event} requires a human channel; this call arrived "
            f"through {channel!r}")
    current = get(relation_id, project_dir=project_dir)
    if current is None:
        raise RelationError(f"unknown relation: {relation_id}")
    if event in ("confirmed", "rejected") and current["state"] == "retracted":
        raise RelationError(
            f"{relation_id} is retracted; it needs a fresh proposal before "
            "any new verdict")
    row = _stamp(event, relation_id, channel, now_ns=now_ns)
    _write(row, project_dir=project_dir)


def confirm(relation_id: str, *, channel: str, project_dir=None,
            now_ns: int | None = None) -> None:
    _human_transition("confirmed", relation_id, channel, project_dir, now_ns)


def reject(relation_id: str, *, channel: str, project_dir=None,
           now_ns: int | None = None) -> None:
    _human_transition("rejected", relation_id, channel, project_dir, now_ns)


def retract(relation_id: str, *, channel: str, project_dir=None,
            now_ns: int | None = None) -> None:
    _human_transition("retracted", relation_id, channel, project_dir, now_ns)


def events(project_dir=None) -> list[dict]:
    """Read valid ledger rows best-effort; malformed lines never sink reads.

    A row without a usable integer `order` is DROPPED, not defaulted: the
    sort would place it at epoch zero, where `proposed` first-writer-wins
    would hand it the record's payload.
    """
    path = _path(project_dir)
    if path is None:
        return []
    # Read first, ask later: `path.exists()` RAISES on an unreadable parent
    # dir (EACCES is not in pathlib's ignored set), which would crash the
    # read-only auditor on exactly the tree it must report on.
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    rows = []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (not isinstance(row, dict)
                or row.get("event") not in EVENTS
                or not _REL_ID_RE.fullmatch(str(row.get("relation_id") or ""))):
            continue
        raw_order = row.get("order")
        if raw_order is None:
            continue
        try:
            row_order = int(raw_order)
        except (TypeError, ValueError):
            continue
        copy = dict(row)
        copy["order"] = row_order
        copy["_line"] = index
        rows.append(copy)
    return rows


def fold(rows: list[dict]) -> dict[str, dict]:
    """Fold relation events into current records, deterministic under reorder.

    `_line` sits behind the unique uuid4 `event_id` in the sort key, so it
    never decides; it keeps the key total for hand-built test rows.
    """
    ordered = sorted(rows, key=lambda row: (
        int(row.get("order") or 0),
        _EVENT_RANK.get(str(row.get("event") or ""), 99),
        str(row.get("event_id") or ""),
        int(row.get("_line") or 0),
    ))
    out: dict[str, dict] = {}
    for row in ordered:
        rel_id = row["relation_id"]
        event = row["event"]
        authority = CHANNEL_AUTHORITY.get(str(row.get("channel") or ""))
        current = out.get(rel_id)
        if event == "proposed":
            proposal = {
                "matcher_version": str(row.get("matcher_version") or ""),
                "matched_by": list(row.get("matched_by") or []),
                "order": row.get("order"),
            }
            if current is None:
                out[rel_id] = {
                    "relation_id": rel_id,
                    "state": "candidate",
                    "type": str(row.get("type") or ""),
                    "from": dict(row.get("from") or {}),
                    "to": dict(row.get("to") or {}),
                    "proposals": [proposal],
                    "contradiction": False,
                    "confirmed_channel": None,
                    "confirmed_author": None,
                    "created_at": row.get("ts"),
                    "updated_at": row.get("ts"),
                    "history_count": 1,
                }
                continue
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            seen = {p["matcher_version"] for p in current["proposals"]}
            if proposal["matcher_version"] not in seen:
                # Accrual, not first-writer-wins: dropping later proposals
                # froze `matched_by` at whatever the FIRST matcher said and
                # made per-version evaluation impossible (refuter round).
                current["proposals"].append(proposal)
            if current["state"] == "retracted":
                # A fresh proposal revives a retraction; a human rejection
                # stays sticky — agents do not get to nag.
                current["state"] = "candidate"
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        if authority != "human":
            continue  # no agent channel moves state, whatever the event says
        if event == "confirmed" and current["state"] != "retracted":
            current["state"] = "confirmed"
            current["confirmed_channel"] = row.get("channel")
            current["confirmed_author"] = row.get("author")
        elif event == "rejected" and current["state"] != "retracted":
            current["state"] = "rejected"
            current["confirmed_channel"] = None
            current["confirmed_author"] = None
        elif event == "retracted":
            current["state"] = "retracted"
            current["confirmed_channel"] = None
            current["confirmed_author"] = None
    # A confirmed directional edge whose confirmed INVERSE also exists is a
    # cycle asserting each item revises the other — invented continuity in
    # its most literal form.  Surfaced on both records, never auto-resolved.
    # #683: self-edges are excluded here too — a confirmed X-to-X edge's
    # inverse hashes to its OWN id (make_id(type, to, from) degenerates to
    # make_id(type, from, to) when from == to), so an unguarded lookup finds
    # itself and flags a lone record against itself. Discovered while adding
    # the item-level pass below, which needs the identical guard; fixed here
    # too so a self-edge never flags regardless of which pass would
    # otherwise have caught it.
    for record in out.values():
        if record["state"] != "confirmed":
            continue
        if record["type"] in SYMMETRIC_TYPES or record["type"] not in TYPES:
            continue
        from_item = record["from"].get("item_id")
        to_item = record["to"].get("item_id")
        if not from_item or not to_item or from_item == to_item:
            continue
        try:
            inverse = make_id(record["type"], record["to"], record["from"])
        except (RelationError, KeyError):
            continue
        twin = out.get(inverse)
        if twin is not None and twin["state"] == "confirmed":
            record["contradiction"] = True
            twin["contradiction"] = True
    # #683: the pass above is SESSION-exact (make_id hashes (session_id,
    # item_id) per endpoint), so a genuine item-level cycle confirmed
    # through two DIFFERENT session pairs never matches its inverse id and
    # slips through unflagged — the docstring's own intent ("a cycle
    # asserting each item revises the other") is item-level, not
    # occurrence-level. This second pass restates the same check at item
    # granularity: index confirmed directional edges by
    # (type, from.item_id, to.item_id), then flag a confirmed record whose
    # REVERSED item-id key is also confirmed — regardless of which
    # session/occurrence carried each side. Same self-edge and symmetric-
    # type guards as the pass above, for the same reasons; "distinct
    # relation id" is checked explicitly too, though the item-id guard
    # already makes a self-match structurally unreachable.
    #
    # Deliberately no oscillation guard: item ids are content-derived (a
    # sha1 of kind+text), so an id names a text, not a moment. A verbatim
    # A->B->A-restated chain is three legitimate revisions that also forms
    # an item-level cycle, and it flags exactly like any other cycle — the
    # marker is attention for human review, not a claim about how the cycle
    # arose, and suppressing it here would hide invented continuity behind
    # a plausible-sounding explanation.
    by_item_key: dict[tuple, list[dict]] = {}
    for record in out.values():
        if record["state"] != "confirmed":
            continue
        if record["type"] in SYMMETRIC_TYPES or record["type"] not in TYPES:
            continue
        from_item = record["from"].get("item_id")
        to_item = record["to"].get("item_id")
        if not from_item or not to_item or from_item == to_item:
            continue
        by_item_key.setdefault(
            (record["type"], from_item, to_item), []).append(record)
    for (type_, from_item, to_item), records_here in by_item_key.items():
        twins = by_item_key.get((type_, to_item, from_item))
        if not twins:
            continue
        for record in records_here:
            for twin in twins:
                if twin["relation_id"] == record["relation_id"]:
                    continue  # pragma: no cover — structurally unreachable:
                    # from_item != to_item (checked above) already means a
                    # record's own key can never equal its reversed key, so
                    # `twins` can never contain `record` itself. Kept as the
                    # explicit "distinct relation id" guard #683 names,
                    # belt for the item-id guard rather than a reachable path.
                record["contradiction"] = True
                twin["contradiction"] = True
    return out


def records(project_dir=None) -> dict[str, dict]:
    return fold(events(project_dir=project_dir))


def get(relation_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(relation_id)


def endpoint_texts(project_dir=None) -> dict:
    """Read-time id→text join over every project surface, live or not.

    The ledger holds no text by construction, so display resolves against
    the checkpoints — and only at render time, never persisted back.  Every
    surface is walked (not just the live checkpoint) because a relation
    endpoint may name a session that only survives in prev-N.
    """
    texts = {}
    for _, _, _, item in store.items_for_project(project_dir):
        item_id = str(item.get("id") or "")
        if item_id and item_id not in texts:
            texts[item_id] = str(item.get("text") or "")
    return texts


def _withhold_erased(records_map, *, project_dir):
    """Split folded records into (renderable, withheld_count).

    Erased means TOMBSTONED, never merely absent: an edge touching a
    forgotten item is withheld from every rendered surface (the count is
    safe — it names no id), while an endpoint that only aged out of the GC
    window still renders as unresolved.
    """
    erased = tombstoned_item_ids(project_dir=project_dir)
    kept, withheld = [], 0
    for record in records_map.values():
        if _row_item_ids(record) & erased:
            withheld += 1
            continue
        kept.append(record)
    return kept, withheld


def listing(*, states=None, project_dir=None) -> tuple[list[dict], int]:
    """Every renderable record in adjudication order: candidates first,
    ties by id, erased edges withheld.  This sort and the withholding are
    the presentation contract shared by the CLI and the viewer lane; they
    live here so the two surfaces cannot drift apart."""
    wanted = set(states or STATES)
    unknown = wanted - STATES
    if unknown:
        raise RelationError(f"unknown state: {', '.join(sorted(unknown))}")
    kept, withheld = _withhold_erased(
        records(project_dir=project_dir), project_dir=project_dir)
    rows = [record for record in kept if record["state"] in wanted]
    rows.sort(key=lambda record: (record["state"] != "candidate",
                                  record["relation_id"]))
    return rows, withheld


def for_item(item_id: str, *, project_dir=None) -> tuple[list[dict], int]:
    """CONFIRMED edges touching one item, erased chains withheld.

    Confirmed-only is the Phase 3 boundary: candidates and rejections stay
    off every reader-facing history surface (they exist for adjudication
    and evaluator metrics), so a chain a reader sees is always one a human
    vouched for.  The withheld count is scoped to THIS item's edges — it
    names no id and never distinguishes forget from absence to a reader."""
    target = str(item_id or "")
    if not target:
        return [], 0
    erased = tombstoned_item_ids(project_dir=project_dir)
    rows, withheld = [], 0
    for record in records(project_dir=project_dir).values():
        touched = _row_item_ids(record)
        if target not in touched:
            continue
        if touched & erased:
            withheld += 1
            continue
        if record["state"] == "confirmed":
            rows.append(record)
    rows.sort(key=lambda record: record["relation_id"])
    return rows, withheld


def tombstoned_item_ids(*, project_dir=None) -> set[str]:
    """Item ids whose LATEST event is a forget tombstone — true erasure.

    Absence from live surfaces is NOT this: per-session files GC and prev-N
    rotates, so an aged-out occurrence must stay a valid, resolvable memory
    (the refuter round: inerting on absence would have destroyed exactly the
    long-range lineage this ledger exists to keep).  Latest-event folding
    means a later `reopen` lifts the tombstone, matching
    `store.forgotten_content_keys`.
    """
    out = set()
    for ref, event in store.resolutions(project_dir=project_dir).items():
        if str(event.get("status") or "").startswith(_FORGOTTEN_PREFIX):
            out.add(str(ref))
    return out


def _row_item_ids(row: dict) -> set[str]:
    out = set()
    for key in ("from", "to"):
        endpoint = row.get(key)
        if isinstance(endpoint, dict):
            item = str(endpoint.get("item_id") or "")
            if item:
                out.add(item)
    return out


def forget_item_id(item_id: str, *, project_dir=None) -> list[str]:
    """Remove every record whose edge touches `item_id` (#678 fork A).

    Rows here hold no text, but an edge is an equivalence CLAIM: a surviving
    `exact-text` rail against a forgotten endpoint asserts the forgotten
    value was identical to a surviving item's text, and post-forget this
    file would be the only surface binding the forgotten id to its sessions,
    kind, and revision-chain length.  #419's rule governs: holding the
    sensitive relation to content, not the file format, is what puts a
    surface inside the deletion contract.

    Rewrites RAW LINES, never `events()` output — the tolerant reader drops
    rows it cannot interpret and stamps `_line`; round-tripping through it
    would silently delete every row a future daimon added (scars 0025/0042).
    Every row of a matched record goes; everything else is written back
    byte-identical, including rows this version cannot interpret.
    Unparseable lines are dropped: already invisible to every read path, and
    `_is_torn` establishes a torn row is expendable.

    No kill-switch check: forget is the ratified deletion exemption (#421).
    Atomic or nothing, staged beside the ledger and swapped with os.replace.
    Returns the relation ids removed, or [] when nothing matched.
    """
    path = _path(project_dir)
    if path is None:
        return []
    target = str(item_id or "")
    if not target:
        return []
    doomed = {
        str(row.get("relation_id") or "")
        for row in events(project_dir=project_dir)
        if target in _row_item_ids(row)
    }
    doomed.discard("")
    if not doomed:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    kept: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (isinstance(row, dict)
                and str(row.get("relation_id") or "") in doomed):
            continue
        kept.append(line)
    tmp = path.with_name(path.name + ".forget-tmp")
    try:
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return []
    return sorted(doomed)
