"""The read view: what a reader may see of a project (#1132 PR 6a).

`snapshot(project)` reads every ledger a withhold decision needs, once, as
data. `classify` turns one checkpoint item and a snapshot into `Visible` or
`Withheld`, and `live` says whether a loop is still open. The projections
(`open`, `team`, `chain`, `lookup`, `match`) are the only shapes a reader is
handed, so a value that must not be shown never leaves this module as text.

Pure and read-only: it never writes, never creates a directory and never
touches the bucket's pointers. A ledger that cannot be read is a HEALTH value
in the snapshot, not an exception; a raise from `view` is a bug.

Nothing imports this yet (PR 6a). `briefing` is imported lazily inside
functions because the briefing path will import `view` later.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType
from typing import Any, Iterator, Literal, Mapping

from . import (amendments, carry, config, jsonl, normalize, requests, schema,
               store, trust)
from .jsonl import Health

# The bucket ledgers a snapshot reports health for, by file name (declared in
# `surfaces`; a test pins that each name is a registered bucket ledger).
LEDGERS = ("events.jsonl", "trust.jsonl", "amendments.jsonl",
           "requests.jsonl", "refutations.jsonl")

Reason = Literal["forgotten", "quarantine", "closed"]


def _frozen(mapping) -> Mapping:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class Snapshot:
    """Every ledger a withhold decision reads, folded once.

    `forgotten` is the set recall uses (every local project plus what
    teammates published); it is derived from the events ledgers too, so a
    fold that raises there empties it and marks events.jsonl UNREADABLE. `quarantined` is this project's own active
    `(kind, value_key)` pairs and `quarantine_ids` names each one's record;
    there is no foreign source yet. `health` maps a ledger file name to what
    `jsonl.read` judged it, or UNREADABLE when its fold raised, and `closed`
    is True when the trust ledger is UNREADABLE: nothing can then be proven
    not quarantined."""

    forgotten: frozenset
    quarantined: frozenset
    quarantine_ids: Mapping
    resolutions: Mapping
    amendments: Mapping
    corroborations: Mapping
    rulings: Any
    requests: Mapping
    health: Mapping
    closed: bool
    details: Mapping = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Snapshot":
        """Nothing forgotten, quarantined or resolved, every ledger absent."""
        return cls(
            forgotten=frozenset(), quarantined=frozenset(),
            quarantine_ids=_frozen({}), resolutions=_frozen({}),
            amendments=_frozen({}), corroborations=_frozen({}),
            rulings=None, requests=_frozen({}),
            health=_frozen({name: Health.ABSENT for name in LEDGERS}),
            closed=False)

    def notes(self) -> tuple[str, ...]:
        """One advisory line per ledger whose health is neither OK nor ABSENT,
        prefixed with a warning sign so a machine parser keeps it as a
        warning. Never carries content: the ledger name, the health word and
        the detail (an errno name or a short reason)."""
        out = []
        for name in LEDGERS:
            state = self.health.get(name, Health.ABSENT)
            if state in (Health.OK, Health.ABSENT):
                continue
            detail = self.details.get(name)
            out.append(f"⚠ {name} is {state.value}"
                       + (f" ({detail})" if detail else ""))
        return tuple(out)

    @cached_property
    def resolved_refs(self) -> frozenset:
        """Refs whose latest event closes the item (`store.is_resolved`)."""
        return frozenset(ref for ref, evt in self.resolutions.items()
                         if store.is_resolved(evt))

    @cached_property
    def fuzzy_events(self) -> tuple:
        """`(item text, event)` of resolved refs that are NOT id-shaped
        (legacy, before id stamping). An id-bearing resolution is fully
        handled by the exact id branch, so its text stays out of the fuzzy
        pool (#145): otherwise a live id-less item that merely resembles a
        closed loop would be silently suppressed."""
        from . import briefing
        shape = briefing._CANDIDATE_ID_SHAPE
        out = []
        for ref in self.resolved_refs:
            if shape.fullmatch(str(ref)):
                continue
            evt = self.resolutions[ref]
            text = str(evt.get("item_text") or "").strip()
            if text:
                out.append((text, evt))
        return tuple(out)

    @cached_property
    def fuzzy_pool(self) -> tuple[str, ...]:
        """The texts of `fuzzy_events`."""
        return tuple(text for text, _evt in self.fuzzy_events)


@dataclass(frozen=True)
class Withheld:
    """An item the reader may not see. It carries identity and the reason, and
    deliberately no text, quote or scene attribute."""

    item_id: str | None
    kind: str
    reason: Reason
    quarantine_id: str | None
    value_key: str


@dataclass(frozen=True)
class Visible:
    item: Any


@dataclass(frozen=True)
class Opened:
    """A checkpoint after the view: `checkpoint` is a copy with every
    withheld item removed (None when there is none), `withheld` names what was
    removed, `suppressed` counts loops closed by a resolution (only when the
    caller asked for `live`). `route` is the route the read was asked to take
    and `fell_back` is the route FACT: the global pointer served the body
    (scar 0058: read it off the result, never reconstruct it)."""

    checkpoint: dict | None
    withheld: tuple
    suppressed: int
    snapshot: Snapshot
    route: store.Route = store.Route.OWN
    fell_back: bool = False


@dataclass(frozen=True)
class Found:
    item: dict
    field: schema.ItemField
    occurrences: tuple


@dataclass(frozen=True)
class Absent:
    pass


@dataclass(frozen=True)
class Match:
    """`hits` are the visible candidates; `withheld` counts the candidates the
    reader may not see, so `len(hits) + withheld` is the raw match count."""

    hits: tuple
    withheld: int


# ---- the snapshot ---------------------------------------------------------


def _bucket(project):
    slug = store.project_slug(config.resolve_project_dir(project))
    return config.checkpoint_dir() / slug if slug else None


def snapshot(project) -> Snapshot:
    """Read every ledger once and fold it. Each fold runs in its own try: a
    raise marks that ledger UNREADABLE and empties its result, and the rest of
    the snapshot is unaffected. Checkpoints are never read here."""
    bucket = _bucket(project)
    health: dict = {}
    details: dict = {}
    reads: dict = {}
    for name in LEDGERS:
        if bucket is None:
            reads[name] = jsonl.Read(Health.ABSENT, [])
        else:
            reads[name] = jsonl.read(bucket / name)
        health[name] = reads[name].health
        if reads[name].detail:
            details[name] = reads[name].detail

    def folded(name, build, default):
        try:
            return build()
        except Exception as exc:  # noqa: BLE001 — a fold's raise is a health state
            health[name] = Health.UNREADABLE
            details[name] = f"fold raised {type(exc).__name__}"
            return default

    rows = reads["events.jsonl"].rows
    resolutions = folded("events.jsonl",
                         lambda: store.fold_resolutions(rows), {})
    corroborations = folded("events.jsonl",
                            lambda: store.fold_corroborations(rows), {})
    records = folded("trust.jsonl",
                     lambda: trust.records(project_dir=project), {})
    active = [r for r in records.values()
              if r.get("state") == "active" and r.get("value_key")]
    quarantine_ids = {(r["kind"], r["value_key"]): r["quarantine_id"]
                      for r in active}
    amend = folded(
        "amendments.jsonl",
        lambda: amendments.render_groups(
            amendments.records(project_dir=project)), {})
    asks = folded("requests.jsonl",
                  lambda: requests.records(project_dir=project), {})

    def read_rulings():
        from . import briefing
        return briefing.rulings_read(project)

    rulings = folded("refutations.jsonl", read_rulings, None)
    forgotten = folded(
        "events.jsonl",
        lambda: frozenset(store.all_forgotten_content_keys()
                          | store.foreign_forgotten_content_keys()),
        frozenset())
    return Snapshot(
        forgotten=forgotten, quarantined=frozenset(quarantine_ids),
        quarantine_ids=_frozen(quarantine_ids),
        resolutions=_frozen(resolutions), amendments=_frozen(amend),
        corroborations=_frozen(corroborations), rulings=rulings,
        requests=_frozen(asks), health=_frozen(health),
        closed=health["trust.jsonl"] is Health.UNREADABLE,
        details=_frozen(details))


# ---- classify / live ------------------------------------------------------


def _entry(item) -> dict:
    """The dict a checkpoint entry matches as: a bare string (a
    contradictions_flagged entry may be one) is its own text."""
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"text": item}
    return {}


def _value_keys(entry: dict) -> list[str]:
    keys = []
    for name in schema.VALUE_FIELDS:
        value = entry.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            keys.append(normalize.content_key(text))
    return keys


def classify(field: schema.ItemField, item, snap: Snapshot) -> Visible | Withheld:
    """Visible or Withheld, by canonical value over `schema.VALUE_FIELDS`.

    Precedence: a closed snapshot withholds everything, then a forgotten
    value (value-only, any field), then a quarantine (scoped to the field's
    kind). A value that is both forgotten and quarantined is reported as
    forgotten: a forgotten item must stay indistinguishable from absent."""
    entry = _entry(item)
    raw_id = entry.get("id")
    item_id = str(raw_id) if raw_id else None
    keys = _value_keys(entry)
    if snap.closed:
        return Withheld(item_id, field.kind, "closed", None,
                        keys[0] if keys else "")
    for key in keys:
        if key in snap.forgotten:
            return Withheld(item_id, field.kind, "forgotten", None, key)
    for key in keys:
        if (field.kind, key) in snap.quarantined:
            return Withheld(item_id, field.kind, "quarantine",
                            snap.quarantine_ids.get((field.kind, key)), key)
    return Visible(item)


def closing_event(item, snap: Snapshot):
    """The resolution event that closes this loop, or None when it is open.
    An id-bearing item binds to a resolution by its own id or not at all (it
    never takes the fuzzy path, even on an exact text coincidence). An
    id-less (legacy) item is closed when its text is the same item as a
    resolved, non-id-shaped ref's recorded text."""
    if not isinstance(item, dict):
        return None
    if item.get("id"):
        if item["id"] in snap.resolved_refs:
            return snap.resolutions[item["id"]]
        return None
    text = str(item.get("text") or "").strip()
    pool = snap.fuzzy_events
    if not text or not pool:
        return None
    generic = carry._generic_terms([t for t, _e in pool] + [text])
    for cand_text, evt in pool:
        if carry._same_item(text, cand_text, generic):
            return evt
    return None


def live(item, snap: Snapshot) -> bool:
    """Is this loop still open? See `closing_event`."""
    return closing_event(item, snap) is None


def prose_verdict(text, snap: Snapshot, *,
                  closed_masks: bool = True) -> Withheld | None:
    """The `Withheld` for free text taken whole, or None when it may be shown.
    The canonical value of the whole string is matched against the forgotten
    set and any quarantine, whatever its kind. A closed snapshot withholds all
    prose unless `closed_masks` is False: a caller whose prose is
    human-ratified policy (the standing rulings) keeps showing it when the
    trust ledger is unreadable, and still masks what is provably forgotten or
    quarantined. The result names a reason and a record, never the value."""
    stripped = str(text or "").strip()
    if snap.closed and closed_masks:
        return Withheld(None, "prose", "closed", None, "")
    if not stripped:
        return None
    key = normalize.content_key(stripped)
    if key in snap.forgotten:
        return Withheld(None, "prose", "forgotten", None, key)
    for kind, value_key in snap.quarantined:
        if key == value_key:
            return Withheld(None, "prose", "quarantine",
                            snap.quarantine_ids.get((kind, value_key)), key)
    return None


def prose_withheld(text, snap: Snapshot) -> bool:
    """Would this free text, taken whole, be a withheld value? A closed
    snapshot withholds all prose."""
    return prose_verdict(text, snap) is not None


# ---- projections ----------------------------------------------------------


def _filter(raw: dict, snap: Snapshot, live_only: bool):
    """A copy of one checkpoint with withheld items removed; returns
    (copy, withheld, suppressed)."""
    out = copy.deepcopy(raw)
    withheld: list = []
    suppressed = 0
    for fld, value in schema.iter_fields(out):
        block = out[fld.section]
        if fld.singleton:
            if not isinstance(value, dict):
                continue
            verdict = classify(fld, value, snap)
            if isinstance(verdict, Withheld):
                withheld.append(verdict)
                del block[fld.key]
            continue
        if not isinstance(value, list):
            continue
        kept = []
        for item in value:
            verdict = classify(fld, item, snap)
            if isinstance(verdict, Withheld):
                withheld.append(verdict)
            elif live_only and not live(item, snap):
                suppressed += 1
            else:
                kept.append(item)
        block[fld.key] = kept
    return out, tuple(withheld), suppressed


def _opened(raw, snap: Snapshot, live_only: bool,
            route: store.Route = store.Route.OWN,
            fell_back: bool = False) -> Opened:
    if not isinstance(raw, dict):
        return Opened(None, (), 0, snap, route, fell_back)
    copy_, withheld, suppressed = _filter(raw, snap, live_only)
    return Opened(copy_, withheld, suppressed, snap, route, fell_back)


def open(project, *, live: bool,  # noqa: A001 — the projection's name
         route: store.Route = store.Route.OWN) -> Opened:
    """The project's latest checkpoint through the view. `live` is required:
    True also drops loops a resolution closed (counted in `suppressed`),
    False keeps them. `route` is the store route: OWN (the default) reads the
    project's own pointer only, OWN_ELSE_GLOBAL may serve the global pointer,
    and `Opened.fell_back` says whether it did. `project` may be a bare slug.
    The snapshot is always the reader's own, whichever body was served."""
    snap = snapshot(project)
    got = store.read_latest_result(project_dir=project, route=route,
                                   admit=store.Admit.ANY)
    return _opened(got.checkpoint, snap, live, route, got.fell_back)


def team(project, *, live: bool) -> tuple:
    """`((author, Opened), ...)` for the teammates' newest checkpoints
    (`store.read_team`, which has already applied the inbound gate)."""
    snap = snapshot(project)
    return tuple((author, _opened(raw, snap, live))
                 for author, raw in store.read_team(project_dir=project))


def _chain_raw(project) -> list[dict]:
    """Every distinct session's checkpoint, newest first. A session appears
    as a flat file and as pointer copies; the flat file wins when it survives
    garbage collection, else the highest-ranked pointer."""
    root = config.checkpoint_dir()
    chosen: dict = {}
    for path in store.project_surfaces(project):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = raw.get("session_id") if isinstance(raw, dict) else None
        if not isinstance(sid, str) or not sid:
            continue
        rank = (int(path.parent == root and path.name == f"{sid}.json"),
                str(path))
        if sid not in chosen or rank > chosen[sid][0]:
            chosen[sid] = (rank, raw)

    def recency(raw: dict):
        return (store._created_epoch(raw.get("created")) or 0.0,
                str(raw.get("session_id") or ""))

    return sorted((entry[1] for entry in chosen.values()),
                  key=recency, reverse=True)


def chain(project, *, live: bool) -> Iterator[Opened]:
    """Every retained checkpoint of the project through the view, newest
    first by the `created` stamp (the order `inspector` walks, so a lookup
    sees the latest wording of an item first)."""
    snap = snapshot(project)
    return iter([_opened(raw, snap, live) for raw in _chain_raw(project)])


def _occurrence(raw: dict) -> tuple:
    return (raw.get("session_id"), raw.get("created"))


def lookup(project, item_id: str) -> Found | Withheld | Absent:
    """The item with this id: Found (the newest copy, plus where every copy
    sits), Withheld when that copy may not be shown, Absent otherwise. The
    occurrences carry session id and created stamp only."""
    snap = snapshot(project)
    newest = None
    occurrences = []
    for raw in _chain_raw(project):
        for fld, item in schema.iter_items(raw, dicts_only=False):
            if isinstance(item, dict) and item.get("id") == item_id:
                occurrences.append(_occurrence(raw))
                if newest is None:
                    newest = (fld, item)
    if newest is None:
        return Absent()
    verdict = classify(newest[0], newest[1], snap)
    if isinstance(verdict, Withheld):
        return verdict
    return Found(newest[1], newest[0], tuple(occurrences))


def match(project, query: str) -> Match:
    """The candidates `resolve` and `reverify` would bind a query to: an exact
    id, else every id-bearing item `carry._same_item` says is the same as the
    query (terms common to all of the checkpoint's texts ignored). The count of
    withheld candidates is returned instead of the candidates."""
    snap = snapshot(project)
    raw = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    if not isinstance(raw, dict):
        return Match((), 0)
    items = [(fld, item) for fld, item in schema.iter_items(raw)
             if not fld.singleton and item.get("id")]
    exact = [(fld, item) for fld, item in items if item["id"] == query]
    if exact:
        candidates = exact[:1]
    else:
        generic = carry._generic_terms(
            [str(item.get("text") or "") for _fld, item in items])
        candidates = [(fld, item) for fld, item in items
                      if carry._same_item(query, str(item.get("text") or ""),
                                          generic)]
    hits = []
    withheld = 0
    for fld, item in candidates:
        if isinstance(classify(fld, item, snap), Withheld):
            withheld += 1
        else:
            hits.append(Found(item, fld, (_occurrence(raw),)))
    return Match(tuple(hits), withheld)
