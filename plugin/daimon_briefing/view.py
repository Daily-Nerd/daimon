"""The read view: what a reader may see of a project (#1132 PR 6a).

`snapshot(project)` reads every ledger a withhold decision needs, once, as
data. `classify` turns one checkpoint item and a snapshot into `Visible` or
`Withheld`, and `live` says whether a loop is still open. The projections
(`open`, `team`, `lookup_many`, `match`) are the only shapes a reader is
handed, so a value that must not be shown never leaves this module as text.

Pure and read-only: it never writes, never creates a directory and never
touches the bucket's pointers. A ledger that cannot be read is a HEALTH value
in the snapshot, not an exception; a raise from `view` is a bug.

`briefing` is imported lazily inside functions because it imports `view` at
module level.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from types import MappingProxyType
from typing import Any, Literal, Mapping, NamedTuple

from . import (amendments, carry, config, display, index_locate, jsonl,
               multihash, normalize, provenance, refutations)
from . import relations as relation_ledger
from . import requests, schema, store, surfaces, trust
from .jsonl import Health
from .surfaces import ReadPosture

Reason = Literal["forgotten", "quarantine", "closed"]

_TOPIC_FIELD = next(f for f in schema.ITEM_FIELDS if f.kind == "topic")


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
    `jsonl.read` judged it, or UNREADABLE when its fold raised, for EVERY
    bucket ledger the registry declares (`surfaces.bucket_ledger_names`).
    `unscannable` holds, for a ledger the read could not vouch for, why
    (`jsonl.Read.cannot_scan`: an errno name, `undecodable`). `closed` is True
    when any ledger's registry read posture is CLOSED: today the trust ledger
    UNREADABLE or TRANSIENT, where nothing can be proven not quarantined.
    `quarantine_items` maps an item id to `(kind, quarantine_id, value_key)`
    for each active quarantine whose record names one (a human passed
    `--item-id`): the hint a lookup uses for an id whose checkpoint copy has
    left the retained window. `forgotten_incomplete` holds the slugs whose
    events ledger cannot be read
    (the machine-wide forget set may miss a tombstone); notes name no slug.
    `index_closed` is True when this bucket's OWN events ledger is unproven:
    only the recall index consults it. `forgotten_ids` are this bucket's item ids whose latest
    event is a `forgotten*` status (a later reopen lifts it): `classify`
    withholds those by id as well as by value. Never machine-wide."""

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
    forgotten_ids: frozenset = frozenset()
    unscannable: Mapping = field(default_factory=dict)
    forgotten_incomplete: frozenset = frozenset()
    index_closed: bool = False
    quarantine_items: Mapping = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Snapshot":
        """Nothing forgotten, quarantined or resolved, every ledger absent."""
        return cls(
            forgotten=frozenset(), quarantined=frozenset(),
            quarantine_ids=_frozen({}), quarantine_items=_frozen({}),
            resolutions=_frozen({}),
            amendments=_frozen({}), corroborations=_frozen({}),
            rulings=None, requests=_frozen({}),
            health=_frozen({name: Health.ABSENT
                            for name in surfaces.bucket_ledger_names()}),
            closed=False)

    def notes(self) -> tuple[str, ...]:
        """One advisory line per ledger whose registry read posture is not
        OPEN in its current state (NOTE, CLOSED or SKIP_SOURCE), then the
        forget-incomplete line, at most `display.NOTE_CAP` lines and a count
        of the rest. Each line is the ledger's name, its state, the detail (an
        errno name or a short reason) and what to do. Never carries content,
        a slug or a path."""
        out = []
        for name in surfaces.bucket_ledger_names():
            state = self.health.get(name, Health.ABSENT)
            if posture(name, state) is ReadPosture.OPEN:
                continue
            out.append(display.ledger_note(
                name, state.value, self.details.get(name, ""),
                self.unscannable.get(name, "")))
        if self.forgotten_incomplete:
            out.append(display.forget_incomplete_note())
        return display.cap_notes(out)

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
    deliberately no text, quote or scene attribute. `receipt` is the textless
    projection of the item's evidence (`_projection`: a valid quote receipt and
    the origin stamps, never a value) that the pointer-window filter takes
    before it removes the item; it is None where no copy was filtered (a
    tombstone-only id, an index row) and is not part of equality."""

    item_id: str | None
    kind: str
    reason: Reason
    quarantine_id: str | None
    value_key: str
    notes: tuple = ()
    receipt: Mapping | None = field(default=None, compare=False)


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
class Resolved:
    """A loop a resolution closed: the item as the checkpoint holds it, its
    field and the closing event. Only a value the reader may see."""

    item: dict
    field: schema.ItemField
    event: dict


@dataclass(frozen=True)
class Suppression:
    """What `status --suppressed` lists. `resolved` are closed loops;
    `withheld` are quarantined values (identity and reason, never text; a
    forgotten value is in neither list and in no count); `closed` counts what
    is hidden because the trust ledger cannot be read (nothing is listed,
    nothing can be proven not quarantined); `candidates` are the #14
    `(list key, item, event)` supersede suggestions, which are not
    resolutions; `notes` are the ledger-health lines."""

    resolved: tuple
    withheld: tuple
    closed: int
    candidates: tuple
    notes: tuple


@dataclass(frozen=True)
class Found:
    """A readable item. `occurrences` are `(session_id, created)` pairs, one
    per distinct session, newest first. `meta` is the envelope of the file the
    copy came from (or the index row's), `source` says which tier answered
    (`pointer` window, `session` file, `index` row) and `notes` are advisory
    codes: `index_only:<reason>` for a row answered from the index. `at` is
    set only by `match`: the `(section, key, index)` of the item in the raw own
    body (`index` is None for the singleton), the locator a write that must
    keep every other byte in place uses; it is not part of equality."""

    item: dict
    field: schema.ItemField
    occurrences: tuple
    meta: store.Meta | None = None
    source: Literal["pointer", "session", "index"] = "pointer"
    notes: tuple = ()
    at: tuple | None = dataclasses.field(default=None, compare=False)


@dataclass(frozen=True)
class Absent:
    """No copy, no row, no tombstone and no quarantine hint for the id. `notes`
    say what could not be looked at: `pointer_unreadable` (a torn pointer),
    `index_unavailable` (no usable index), `index_closed` (the trust ledger
    cannot be read, so the index and the hints were not consulted),
    `outside_window` (the pointer window and the index hold no copy, so a
    quarantine of the value that names no item id is not visible from here),
    `unreadable` and `foreign-stamp` (an index row whose session file is torn
    or stamped for another project; nothing of the row is used)."""

    notes: tuple = ()


@dataclass(frozen=True)
class Match:
    """What a binding verb finds for a query in the live own body. `hits` are
    the visible candidates; `withheld` counts the quarantined and closed ones
    the reader may not see (a forgotten candidate is in no count: it is
    absent); `exact` is set when the query is an exact id that the body holds
    and that is quarantined or closed; `closed` says the trust ledger cannot
    be read, in which case `withheld` and `exact` cover every candidate and
    the caller prints no count (it would be a membership oracle). `sole` is
    the one candidate a verb may bind without guessing."""

    hits: tuple
    withheld: int
    exact: "Withheld | None" = None
    closed: bool = False

    @property
    def sole(self) -> "Found | None":
        """The never-guess rule: a bind happens only when exactly one candidate
        is visible and none is withheld. A visible hit beside a withheld one is
        ambiguous, never a bind."""
        if len(self.hits) == 1 and self.withheld == 0:
            return self.hits[0]
        return None


# ---- the snapshot ---------------------------------------------------------


def _bucket(project):
    slug = store.project_slug(config.resolve_project_dir(project))
    return config.checkpoint_dir() / slug if slug else None


def _is_bucket(name: str) -> bool:
    try:
        (config.checkpoint_dir() / name / store._LATEST).stat()
    except OSError:
        return False
    return True


def buckets(own: str | None) -> tuple[str, ...]:
    """The bucket names a caller may enumerate, sorted: every subdirectory of
    the checkpoint dir that holds a `latest.json` (a torn pointer still counts,
    as in `store.list_buckets`). The one enumeration and the one tenant rule
    (#899): under `config.tenant_scoped()` it is the caller's own bucket `own`
    when that exists, and nothing else, so a listing cannot name another
    tenant. Names only; no checkpoint body is read."""
    if config.tenant_scoped():
        return (own,) if own and bucket_exists(own, own) else ()
    try:
        names = sorted(p.name for p in config.checkpoint_dir().iterdir())
    except OSError:
        return ()
    return tuple(n for n in names if _is_bucket(n))


def bucket_exists(slug: str, own: str | None) -> bool:
    """Whether `slug` names a bucket the caller may open: one path segment
    holding a `latest.json`, and under tenant scope only `own`. A stat, not a
    scan: a request that already knows the name does not list the others."""
    if not slug or slug in (".", "..") or "/" in slug or "\\" in slug:
        return False
    if config.tenant_scoped() and slug != own:
        return False
    return _is_bucket(slug)


def read_scopes(project, *, all_projects: bool = False) -> list[str] | None:
    """The slugs an UNADDRESSED read may see (#899): this project's own plus
    whatever the host declared in DAIMON_EXTRA_READ_SLUGS, own first. None
    when the project is unknown, so each caller keeps its own "unknown" rule
    (search: no filter; lookup: None; suggest: silence) and the allowlist can
    never turn an unscoped read into a read of the listed buckets.

    `all_projects` asks for no filter at all (None). Under
    `config.tenant_scoped()` it is ignored: a caller-chosen cross-project
    address is a cross-tenant read, so the entry points refuse it and this is
    the same rule one layer down: an in-process caller that passes
    `all_projects=True` under tenant scope is narrowed to own + extras, where
    the CLI and MCP entry points refuse out loud. An explicit `slug` is the scope itself and
    never comes through here."""
    if all_projects and not config.tenant_scoped():
        return None
    own = store.project_slug(config.resolve_project_dir(project))
    if own is None:
        return None
    scopes = [own]
    for extra in config.extra_read_slugs():
        if extra not in scopes:
            scopes.append(extra)
    return scopes


def forgotten_keys() -> frozenset:
    """The machine-wide forgotten set, the one `snapshot` reads: every local
    project's tombstones plus what teammates published. Memoized in `store`,
    so a caller that needs it for many buckets (`projects`) asks once."""
    return frozenset(store.all_forgotten_content_keys()
                     | store.foreign_forgotten_content_keys())


def forgotten_ids(resolutions) -> frozenset:
    """The refs of a `store.fold_resolutions` result whose latest event is a
    forget tombstone that still stands (`store.is_tombstone_status`, the
    predicate the forget writer and the tombstone readers use; a later reopen
    lifts it). A free-form status that merely starts with the word
    ("forgotten about it") is a resolution, not a tombstone. The one id rule,
    shared by `snapshot` and `judge`."""
    return store.tombstone_refs(resolutions)


def posture(name: str, health: Health, *, foreign: bool = False) -> ReadPosture:
    """What a reader does with bucket ledger `name` in `health`: the registry's
    read column (`foreign` asks the column for a ledger read across buckets).
    An OK ledger is OPEN. A name the registry never declared raises
    LookupError: asking is a bug, not an empty answer."""
    return surfaces.read_posture(surfaces.bucket_ledger(name), health.value,
                                 foreign=foreign)


def unproven(health: Health) -> bool:
    """TRANSIENT or any UNREADABLE state: the ledger was not (fully) read, so
    the absence of a row proves nothing. OK, ABSENT and DEGRADED are proven."""
    return health in (Health.TRANSIENT, Health.UNREADABLE)


def _trust_index(project, read: jsonl.Read) -> tuple:
    """`(quarantine_ids, quarantine_items, health, detail)` for one bucket's
    trust ledger: the active quarantines as `{(kind, value_key):
    quarantine_id}`, the active ones that name an item id as `{item_id: (kind,
    quarantine_id, value_key)}`, and what the ledger is. `read` is `jsonl.read` of trust.jsonl; a fold that raises marks
    the ledger UNREADABLE, which closes the view. The one place the quarantine
    rule and that closing rule live, for `snapshot` and `_light` alike; the
    fold is `trust.records`, never a copy of it."""
    health, detail = read.health, read.detail
    try:
        records = trust.records(project_dir=project, read=read)
    except Exception as exc:  # noqa: BLE001 — a fold's raise is a health state
        records = {}
        health, detail = Health.UNREADABLE, f"fold raised {type(exc).__name__}"
    active = [r for r in records.values()
              if r.get("state") == "active" and r.get("value_key")]
    ids = {(r["kind"], r["value_key"]): r["quarantine_id"] for r in active}
    items = {r["item_id"]: (r["kind"], r["quarantine_id"], r["value_key"])
             for r in active if r.get("item_id")}
    return ids, items, health, detail


def snapshot(project) -> Snapshot:
    """Read every bucket ledger the registry declares once and fold it. Each
    fold runs in its own try: a raise marks that ledger UNREADABLE and empties
    its result, and the rest of the snapshot is unaffected. The folds take the
    rows already read (the ledgers' `rows=` seams), so no file is read twice.
    Checkpoints are never read here."""
    bucket = _bucket(project)
    names = surfaces.bucket_ledger_names()
    reads = {name: (jsonl.Read(Health.ABSENT, []) if bucket is None
                    else jsonl.read(bucket / name))
             for name in names}
    health = {name: read.health for name, read in reads.items()}
    details = {name: read.detail for name, read in reads.items() if read.detail}
    unscannable = {name: read.cannot_scan for name, read in reads.items()
                   if read.cannot_scan}

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
    (quarantine_ids, quarantine_items, health["trust.jsonl"],
     detail) = _trust_index(project, reads["trust.jsonl"])
    if detail:
        details["trust.jsonl"] = detail
    refut = reads["refutations.jsonl"]
    policy = folded(
        "refutations.jsonl",
        lambda: refutations.request_policy_history(
            project, rows=refut.rows,
            tombstone_rows=reads["request_policy_tombstones.jsonl"].rows,
            unscannable=refut.cannot_scan), frozenset())
    amend = folded(
        "amendments.jsonl",
        lambda: amendments.render_groups(amendments.records(
            project_dir=project, rows=reads["amendments.jsonl"].rows)), {})
    asks = folded(
        "requests.jsonl",
        lambda: requests.records(project_dir=project,
                                 rows=reads["requests.jsonl"].rows,
                                 policy=policy), {})

    def read_rulings():
        from . import briefing
        return briefing.rulings_read(project, read=refut)

    rulings = folded("refutations.jsonl", read_rulings, None)
    forgotten = folded("events.jsonl", forgotten_keys, frozenset())
    ids = folded("events.jsonl", lambda: forgotten_ids(resolutions),
                 frozenset())
    incomplete = folded("events.jsonl", store.forgotten_incomplete,
                        frozenset())
    return Snapshot(
        forgotten=forgotten, quarantined=frozenset(quarantine_ids),
        quarantine_ids=_frozen(quarantine_ids),
        quarantine_items=_frozen(quarantine_items),
        resolutions=_frozen(resolutions), amendments=_frozen(amend),
        corroborations=_frozen(corroborations), rulings=rulings,
        requests=_frozen(asks), health=_frozen(health),
        closed=any(posture(n, h) is ReadPosture.CLOSED
                   for n, h in health.items()),
        details=_frozen(details), forgotten_ids=ids,
        unscannable=_frozen(unscannable),
        forgotten_incomplete=incomplete,
        index_closed=unproven(health["events.jsonl"]))


class LedgerState(NamedTuple):
    """What one ledger file is: its health, the detail (an errno name or a
    short reason) and why a scan could not vouch for it ("" when it can)."""
    health: Health
    detail: str
    unscannable: str


def ledger_states(project, names=None) -> dict:
    """Each bucket ledger file name of `project` (or just `names`) to its
    `LedgerState`. No fold, no rows: a caller that needs to say what is wrong
    with a ledger and what to do about it (`status`) asks here, never
    `jsonl.read`."""
    bucket = _bucket(project)
    out = {}
    for name in (names or surfaces.bucket_ledger_names()):
        read = (jsonl.Read(Health.ABSENT, []) if bucket is None
                else jsonl.read(bucket / name))
        out[name] = LedgerState(read.health, read.detail, read.cannot_scan)
    return out


def ledger_health(project) -> dict:
    """Each bucket ledger file name of `project` to what `jsonl.read` judges
    it, as the `Health` value word ("ok", "absent", "degraded", "transient",
    "unreadable"). A caller (anamnesis) asks before it writes whether the
    ledger it is about to append to can be trusted."""
    return {name: state.health.value
            for name, state in ledger_states(project).items()}


def _stat_key(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    # ctime too: a chmod that makes a ledger unreadable (or readable again)
    # moves it, and nothing else about the file.
    return (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)


@dataclass(frozen=True)
class Judge:
    """What one bucket's reader may see, as data a caller holds across many
    rows: the same `Snapshot` fields `classify` reads (the machine forgotten
    set, this bucket's forgotten ids and active quarantines, `closed`) and
    nothing else. `empty` is the fast path: when True no row of the bucket
    can be withheld, so a caller skips the per-row work. `empty` ignores
    `index_closed` on purpose: the bucket's own rows are not withheld for the
    briefing (D3), so a recall caller must check `index_closed` before taking
    the fast path, as `recall._judge_rows` and `recall._build` do."""

    snap: Snapshot

    @property
    def empty(self) -> bool:
        snap = self.snap
        return not (snap.closed or snap.forgotten or snap.forgotten_ids
                    or snap.quarantined)

    @property
    def closed(self) -> bool:
        return self.snap.closed

    @property
    def index_closed(self) -> bool:
        """This bucket's own events ledger is unproven (TRANSIENT or
        UNREADABLE): its tombstones cannot all be known. Separate from
        `closed` on purpose: only recall's build and query consult it (the
        index holds no rows for the bucket), so `why`, the viewer and the
        briefing keep reading the bucket through its good lines."""
        return self.snap.index_closed

    def verdict(self, fld: schema.ItemField, item) -> Visible | Withheld:
        return classify(fld, item, self.snap)


# (checkpoint root, slug) -> (memo key, Judge). Only OK or ABSENT ledgers are
# kept: a ledger that is unreadable or still failing is read again on the next
# call, so a repair or a retry shows at once.
_judge_memo: dict[tuple, tuple] = {}


def _is_bare_slug(slug) -> bool:
    """A bucket name: one path segment. A row stamp such as `/a/b` (a literal
    path from an older writer, or a hostile foreign file) would resolve against
    the working directory and judge the wrong bucket, so it is judged by the
    forgotten set alone."""
    return (isinstance(slug, str) and bool(slug) and slug not in (".", "..")
            and "/" not in slug and "\\" not in slug)


def _read_judge(slug, forgotten=None) -> tuple[Judge, bool]:
    """`(judge, memoizable)` for a bucket slug, read fresh. A fold that raises
    (the machine forgotten set, this bucket's forgotten ids) cannot prove that
    nothing is forgotten, so the judge is closed and not memoized."""
    bucket = _bucket(slug) if slug else None
    proven = True
    if forgotten is None:
        try:
            forgotten = forgotten_keys()
        except Exception:  # noqa: BLE001
            forgotten, proven = frozenset(), False
    if bucket is None:
        return (Judge(dataclasses.replace(Snapshot.empty(),
                                          forgotten=forgotten,
                                          closed=not proven)), proven)
    trust_read = jsonl.read(bucket / "trust.jsonl")
    events_read = jsonl.read(bucket / "events.jsonl")
    ids, items, health, detail = _trust_index(slug, trust_read)
    try:
        folded = forgotten_ids(store.fold_resolutions(events_read.rows))
    except Exception:  # noqa: BLE001
        folded, proven = frozenset(), False
    # The light snapshot carries the two ledgers it reads: their health,
    # detail and why a scan could not vouch for them.
    reads = {"trust.jsonl": (health, detail, trust_read.cannot_scan),
             "events.jsonl": (events_read.health, events_read.detail,
                              events_read.cannot_scan)}
    snap = dataclasses.replace(
        Snapshot.empty(), forgotten=forgotten, quarantined=frozenset(ids),
        quarantine_ids=_frozen(ids), quarantine_items=_frozen(items),
        forgotten_ids=folded,
        health=_frozen({**Snapshot.empty().health,
                        **{n: r[0] for n, r in reads.items()}}),
        details=_frozen({n: r[1] for n, r in reads.items() if r[1]}),
        unscannable=_frozen({n: r[2] for n, r in reads.items() if r[2]}),
        index_closed=unproven(events_read.health),
        closed=(posture("trust.jsonl", health) is ReadPosture.CLOSED
                or not proven))
    steady = (Health.OK, Health.ABSENT)
    return Judge(snap), (proven and health in steady
                         and events_read.health in steady)


def judge(slug, *, stamp=None, forgotten=None, incomplete=None) -> Judge:
    """The verdict source for one bucket (`slug`; None or a name with no
    bucket judges the forgotten set alone). Memoized on the stat of the
    bucket's `trust.jsonl` and `events.jsonl` and of everything that feeds the
    machine-wide forgotten set, so a warm call is a handful of `stat`s. An
    UNREADABLE or transient result is never memoized. `stamp` is
    `store.forgotten_stamp()` taken by a caller that judges many buckets in
    one pass (a build, a query, a listing), so it is computed once for the
    pass; `forgotten` is `forgotten_keys()` taken the same way. A trust ledger
    that is UNREADABLE or still failing after the retries (TRANSIENT, no rows)
    closes the judge: nothing can be proven not quarantined. `incomplete` is
    `store.forgotten_incomplete()` taken by the same caller for the same pass
    (a build, a query, a listing), so a judge does not walk every bucket's
    events ledger again to learn it."""
    if not _is_bare_slug(slug):
        slug = None   # a stamp that is not a bucket name routes nowhere
    bucket = _bucket(slug) if slug else None
    if bucket is None:
        return _read_judge(slug, forgotten)[0]
    memo_slot = (str(config.checkpoint_dir()), slug)
    key = (_stat_key(bucket / "trust.jsonl"), _stat_key(bucket / "events.jsonl"),
           stamp if stamp is not None else store.forgotten_stamp())
    hit = _judge_memo.get(memo_slot)
    if hit is not None and hit[0] == key:
        return hit[1]
    got, memoizable = _read_judge(slug, forgotten)
    # A judge built while some bucket's events ledger could not be read holds
    # a forget set that may be short; the stat key cannot see that ledger
    # become readable again (a permission fix changes no mtime or size), so it
    # is not kept (D10.2).
    still = (incomplete if incomplete is not None
             else store.forgotten_incomplete())
    if memoizable and not still:
        _judge_memo[memo_slot] = (key, got)
    else:
        _judge_memo.pop(memo_slot, None)
    return got


def _light(slug, forgotten, stamp=None, incomplete=None) -> Snapshot:
    """A snapshot with only what `classify` reads: the forgotten set the
    caller holds and the bucket's judgement (`judge`): its forgotten ids, its
    active quarantines and `closed`."""
    return dataclasses.replace(
        judge(slug, stamp=stamp, forgotten=forgotten,
              incomplete=incomplete).snap,
        forgotten=forgotten)


def _topic_text(checkpoint: dict, snap: Snapshot) -> str | None:
    """The active topic text of a checkpoint, or None when it has none or
    `classify` withholds it under `snap`."""
    context = checkpoint.get("working_context")
    topic = context.get("active_topic") if isinstance(context, dict) else None
    if not isinstance(topic, dict) or isinstance(
            classify(_TOPIC_FIELD, topic, snap), Withheld):
        return None
    shown = topic.get("text")
    return shown if isinstance(shown, str) else None


@dataclass(frozen=True)
class Peek:
    """What a listing may show of one checkpoint: the active topic text (None
    for no checkpoint, no topic and every withheld case) and how many list
    items a reader may see. A withheld item is in neither, so a count never
    reveals that one exists."""

    topic: str | None
    visible_items: int


def _peek_under(checkpoint: dict, snap: Snapshot) -> Peek:
    text = _topic_text(checkpoint, snap)
    count = sum(
        1 for fld, item in schema.iter_items(checkpoint, dicts_only=False)
        if not fld.singleton and isinstance(classify(fld, item, snap), Visible))
    return Peek(text, count)


def peek(checkpoint, slug, *, forgotten, stamp=None,
         snap: Snapshot | None = None) -> Peek:
    """The topic and the visible item count of a checkpoint the caller already
    holds, as a reader of bucket `slug` may see them. A hidden topic reads as
    an absent one (forgotten, quarantined, trust ledger unreadable). `forgotten`
    is `forgotten_keys()`, computed once by a caller that lists many buckets.
    It classifies with `classify` over a light snapshot, so a listing pays one
    small ledger read per bucket instead of a whole `snapshot`, and a row's
    other fields, its topic and its count come from the same read of the
    checkpoint. Never raises for data health."""
    if not isinstance(checkpoint, dict):
        return Peek(None, 0)
    return _peek_under(checkpoint, snap if snap is not None
                       else _light(slug, forgotten, stamp))


@dataclass(frozen=True)
class Listed:
    """One bucket of a project listing: the envelope furniture of its latest
    pointer plus its `Peek`. `readable` is False for a torn pointer, which
    stays listed (hiding a bucket would read as no such project)."""

    slug: str
    mtime: float
    readable: bool
    name: Any
    session_id: Any
    created: Any
    git_branch: Any
    peek: Peek
    closed: bool = False


def projects(own: str | None) -> tuple[Listed, ...]:
    """The buckets a caller may list (`buckets(own)`, the one tenant rule), each
    with its latest pointer's envelope and `peek`. A checkpoint body is read
    only for an allowed bucket: another tenant's pointer is never opened.
    Unsorted; ordering is a display concern."""
    forgotten = forgotten_keys()
    stamp = store.forgotten_stamp()   # once for the listing, not per bucket
    incomplete = store.forgotten_incomplete()   # likewise
    out = []
    for b in store.list_buckets(only=frozenset(buckets(own))):
        cp = b["checkpoint"]
        data = cp if isinstance(cp, dict) else {}
        # One light snapshot per bucket serves the peek AND the closed flag
        # (a torn pointer's bucket can still have a trust ledger that
        # cannot be read).
        snap = _light(b["slug"], forgotten, stamp, incomplete)
        out.append(Listed(
            b["slug"], b["mtime"], cp is not None, data.get("project_name"),
            data.get("session_id"), data.get("created"),
            data.get("git_branch"),
            peek(cp, b["slug"], forgotten=forgotten, stamp=stamp, snap=snap)
            if cp is not None else Peek(None, 0),
            snap.closed))
    return tuple(out)


def forgotten_notes() -> tuple[str, ...]:
    """`forget-incomplete` when some bucket's events ledger cannot be read, so
    the machine-wide forget set may be missing a tombstone; else nothing."""
    return (display.forget_incomplete_note(),) if store.forgotten_incomplete() \
        else ()


def projects_notes(own: str | None, listed=None) -> tuple[str, ...]:
    """The notes a project listing carries: `projects-closed` for the listed
    buckets whose trust ledger cannot be read (a count, except under tenant
    scope, where it would count buckets the caller may not list) and
    `forget-incomplete`. `listed` is the caller's `projects(own)` when it
    already holds it."""
    listed = projects(own) if listed is None else listed
    closed = sum(1 for b in listed if b.closed)
    lines = []
    if closed:
        lines.append(display.projects_closed_note(
            closed, config.tenant_scoped()))
    return display.cap_notes([*lines, *forgotten_notes()])


def team_notes(project=None) -> tuple[str, ...]:
    """`team-closed` when `project`'s own events ledger cannot be read (no
    teammate is shown), `author-skipped` / `author-degraded` for the
    teammates' published tombstone ledgers: an author whose ledger cannot be
    read is not admitted (O3); one whose ledger has torn lines is read
    around."""
    tombs = store.foreign_tombstones()
    lines = []
    if project is not None:
        bucket = _bucket(project)
        if bucket is not None and unproven(
                jsonl.read(bucket / "events.jsonl").health):
            lines.append(display.team_closed_note())
    if tombs.unproven:
        lines.append(display.author_skipped_note())
    if tombs.degraded:
        lines.append(display.author_degraded_note())
    return tuple(lines)


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
    kind). An item whose id is in `snap.forgotten_ids` is forgotten too, even
    when its value is no longer the one that was tombstoned. A value that is
    both forgotten and quarantined is reported as forgotten: there is nothing
    left to review. The reason is returned so the exact-id history verbs
    (`why`, `blame`, the viewer's why page) can say `forgotten` for the one id
    asked about; every listing (`open`, `match`, `diff`, recall) drops a
    forgotten item as if it were absent."""
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
    if item_id is not None and item_id in snap.forgotten_ids:
        return Withheld(item_id, field.kind, "forgotten", None,
                        keys[0] if keys else "")
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


def _projection(item) -> dict | None:
    """What a withheld item may still say about how it was captured, with no
    text: its quote receipt when that is structurally valid (outcome, verifier,
    source locator, digest and bound message ids, none of which is the value)
    and the origin stamps. An invalid receipt is dropped whole, since its
    fields are unchecked."""
    if not isinstance(item, dict):
        return None
    receipt = item.get("quote_provenance")

    def stamp(name):
        value = item.get(name)
        return value if isinstance(value, str) else None

    return {"quote_provenance": (copy.deepcopy(receipt)
                                 if provenance.valid_quote_receipt(receipt)
                                 else None),
            "origin_session": stamp("origin_session"),
            "origin_author": stamp("origin_author")}


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
                withheld.append(dataclasses.replace(
                    verdict, receipt=_projection(value)))
                del block[fld.key]
            continue
        if not isinstance(value, list):
            continue
        kept = []
        for item in value:
            verdict = classify(fld, item, snap)
            if isinstance(verdict, Withheld):
                withheld.append(dataclasses.replace(
                    verdict, receipt=_projection(item)))
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


@dataclass(frozen=True)
class Pointer:
    """One rotation pointer of a bucket (`latest`, `prev-N`) through the view.
    `meta` is its envelope and `opened` its body judged like `open`; both are
    None / empty-bodied when the file is torn (`readable` False), which stays
    listed so a short window is not mistaken for a complete one."""

    ref: str
    readable: bool
    meta: store.Meta | None
    opened: Opened


def _pointer_order(path) -> int:
    ref = path.name.removesuffix(".json")
    return 0 if ref == "latest" else int(ref.split("-")[1])


def pointers(project, *, snap: Snapshot | None = None) -> tuple[Pointer, ...]:
    """The project's pointer window, `latest` then `prev-1`, `prev-2`, ..., each
    through the view with one shared snapshot (`snap`, or a fresh full one: a
    caller that already holds the snapshot of its read passes it). Torn
    pointers are listed unreadable; a project with no bucket has none."""
    bucket = _bucket(project)
    if bucket is None:
        return ()
    try:
        paths = sorted((p for p in bucket.iterdir()
                        if store._POINTER_RE.match(p.name)),
                       key=_pointer_order)
    except OSError:
        return ()
    snap = snap if snap is not None else snapshot(project)
    out = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        meta = (store.Meta(*(raw.get(n) for n in store.Meta._fields))
                if isinstance(raw, dict) else None)
        out.append(Pointer(path.name.removesuffix(".json"), meta is not None,
                           meta, _opened(raw if meta else None, snap, False)))
    return tuple(out)


@dataclass(frozen=True)
class SessionRow:
    """One session file in a listing: the file stem, the `created` stamp and
    the topic a reader may see (None when absent or withheld)."""

    session_id: str
    created: Any
    topic: str | None


@dataclass(frozen=True)
class Sessions:
    """`rows` newest first; `unreadable` counts session files that cannot be
    parsed (their project cannot be told, so every project is told; under tenant
    scope none is counted); `notes` are the ledger-health lines of the snapshot the topics were judged by."""

    rows: tuple
    unreadable: int
    notes: tuple


def sessions(project) -> Sessions:
    """A light listing of the project's session files: no body is copied or
    filtered, only each topic is classified. Membership is the payload's
    `project_slug`, as in `store.project_surfaces`; a file that does not parse
    is counted, since nothing says whose it was. Under tenant scope (#899) it
    is not counted: a count of files the caller cannot attribute to its own
    bucket would report activity in buckets it may not see."""
    slug = store.project_slug(config.resolve_project_dir(project))
    root = config.checkpoint_dir()
    try:
        files = store._session_files(root)
    except OSError:
        files = []
    snap = snapshot(project)
    tenant_scoped = config.tenant_scoped()
    rows, unreadable = [], 0
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if not tenant_scoped:
                unreadable += 1
            continue
        if not isinstance(raw, dict) or raw.get("project_slug") != slug:
            continue
        rows.append(SessionRow(path.stem, raw.get("created"),
                               _topic_text(raw, snap)))

    def newest(row: SessionRow):
        return (row.created if isinstance(row.created, str) else "",
                row.session_id)

    rows.sort(key=newest, reverse=True)
    return Sessions(tuple(rows), unreadable, snap.notes())


def open_sessions(project, session_ids, *, live: bool,
                  snap: Snapshot | None = None) -> dict:
    """`{session_id: Opened}` for the named session files that exist, parse and
    belong to the project, one snapshot for all (`snap`, or a fresh full one).
    An id that names nothing the caller may open (a torn file, another
    project's, an escape from the store) is absent from the result."""
    slug = store.project_slug(config.resolve_project_dir(project))
    snap = snap if snap is not None else snapshot(project)
    out = {}
    for sid in session_ids:
        raw = (store.read_checkpoint(sid)
               if sid and sid == store._safe_name(sid) else None)
        if isinstance(raw, dict) and raw.get("project_slug") == slug:
            out[sid] = _opened(raw, snap, live)
    return out


# ---- receipt check ----------------------------------------------------------

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def receipt_state(checkpoint) -> dict:
    """The viewer's tamper check of one checkpoint's receipt: `{"state":
    "unsigned" | "missing" | "match" | "mismatch", "detail": str | None}`. The
    sidecar (`<session_id>.receipt`) must be present and its outputs_hash must
    cover the bytes of the session's ROOT file (`<checkpoint dir>/
    <session_id>.json`): a receipt is a statement about a SESSION, not about
    whichever pointer copy was opened, and only the final root bytes are
    bound. No `receipts` marker is quiet (unsigned); a broken claim is loud.
    Signature verification is `daimon verify-receipt` and needs the vitni CLI;
    this is a file read and a sha256. Never raises."""
    if not isinstance(checkpoint, dict) or checkpoint.get("receipts") is not True:
        return {"state": "unsigned", "detail": None}
    sid = checkpoint.get("session_id")
    # sid arrives from file content and is about to be joined to a path, twice.
    if not isinstance(sid, str) or not _SESSION_ID_RE.fullmatch(sid):
        return {"state": "missing", "detail": None}
    root = config.checkpoint_dir() / f"{sid}.json"
    sidecar = root.with_suffix(".receipt")
    try:
        want = json.loads(sidecar.read_text(encoding="utf-8"))["receipt"]["outputs_hash"]
        if not isinstance(want, str):
            raise KeyError("outputs_hash")
    except FileNotFoundError:
        return {"state": "missing", "detail": None}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"state": "missing",
                "detail": f"{sidecar.name} is not a readable receipt"}
    except (KeyError, TypeError):
        # The sidecar parsed as JSON and just does not carry outputs_hash: not
        # an unreadable file, so it gets no detail claiming otherwise.
        return {"state": "missing", "detail": None}
    try:
        got = multihash.sha256(root.read_bytes())
    except OSError:
        return {"state": "missing", "detail": None}
    return {"state": "match" if got == want else "mismatch", "detail": None}


# ---- ledger rows ------------------------------------------------------------

# The marker `store.scrub_event_fields` leaves where a forgotten value was: it
# carries the tombstoned key, so a reader never shows it.
_MARKER_HEAD, _MARKER_TAIL = store._FORGOTTEN_FIELD_MARKER.split("{}")
_SCRUBBED = re.compile(
    re.escape(_MARKER_HEAD) + "[0-9a-f]+" + re.escape(_MARKER_TAIL))


@dataclass(frozen=True)
class Event:
    """One `events.jsonl` row. `note`, `item_text` and `status` are the
    declared prose columns, already judged: a quarantined or closed value is
    the withheld marker, a forgotten or scrubbed one is None. A forget
    tombstone (`tombstone`) has the bare status `forgotten` and no item text:
    its status names the key of the value it tombstones. A column that is not
    text is None."""

    ts: str | None
    kind: str | None
    item_ref: str | None
    status: str | None
    source: str | None
    note: str | None
    item_text: str | None
    tombstone: bool = False


@dataclass(frozen=True)
class Verification:
    """One `verification.jsonl` row: a pointer and a reason code, never the
    rejected text (the ledger holds no plaintext, so nothing here is judged)."""

    ts: str | None
    item_ref: str | None
    check: str | None
    reason: str | None


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _judged(value, snap: Snapshot, *, closed_masks: bool) -> str | None:
    """A prose column as a reader may see it: None for absent, forgotten or
    scrubbed text, the withheld marker for a quarantined (or, when
    `closed_masks`, any value under a closed snapshot), else the text."""
    text = _text(value)
    if text is None or _SCRUBBED.search(text):
        return None
    verdict = prose_verdict(text, snap, closed_masks=closed_masks)
    if verdict is None:
        return text
    return None if verdict.reason == "forgotten" else display.withheld_marker(
        verdict)


def _event(row: dict, snap: Snapshot) -> Event:
    status = _text(row.get("status"))
    tombstone = store.is_tombstone_status(status)
    if tombstone:
        status, item_text = "forgotten", None
    else:
        # Status is a lifecycle word, not an item value: a closed ledger does
        # not mask it, a forgotten or quarantined whole value does. A scrubbed
        # status keeps the class token `scrub_event_fields` preserved.
        status = _judged(_SCRUBBED.sub("", status).strip() if status else None,
                         snap, closed_masks=False)
        item_text = _judged(row.get("item_text"), snap, closed_masks=True)
    return Event(_text(row.get("ts")), _text(row.get("kind")),
                 _text(row.get("item_ref")), status, _text(row.get("source")),
                 _judged(row.get("note"), snap, closed_masks=False),
                 item_text, tombstone)


def events(project, *, snap: Snapshot | None = None) -> tuple[Event, ...]:
    """The project's `events.jsonl` rows in file order, the prose columns
    judged over one snapshot (`snap`, or a fresh one: a caller that already
    holds the snapshot of its read passes it). Notes are human prose, so they
    stay readable when the trust ledger is unreadable; an item value does not.
    An absent ledger is no rows; torn lines and non-object rows are skipped."""
    bucket = _bucket(project)
    if bucket is None:
        return ()
    snap = snap if snap is not None else snapshot(project)
    return tuple(_event(row, snap)
                 for row in jsonl.read(bucket / "events.jsonl").rows
                 if isinstance(row, dict))


def latest_event(snap: Snapshot, item_id: str) -> Event | None:
    """The judged latest lifecycle event of a ref, the one `snap.resolutions`
    folded to (None for a ref with none). The lifecycle word of the same ref
    comes from the same row (`lifecycle_word(snap.resolutions.get(ref))`)."""
    row = snap.resolutions.get(item_id)
    return _event(row, snap) if row is not None else None


def item_events(project, item_id: str, *,
                snap: Snapshot | None = None) -> tuple[Event, ...]:
    """The `events.jsonl` rows addressed to EXACTLY `item_id`, judged like
    `events` and in the order the lifecycle fold uses (`store.fold_item_events`:
    by `ts`, an unstamped row oldest, file position breaking a tie), never in
    file order. A namespaced ref (a corroboration row) never answers for the
    bare id. No bucket or no ref is no rows."""
    bucket = _bucket(project)
    if bucket is None or not item_id:
        return ()
    snap = snap if snap is not None else snapshot(project)
    rows = store.fold_item_events(
        jsonl.read(bucket / "events.jsonl").rows, item_id)
    return tuple(_event(row, snap) for row in rows)


def verifications(project) -> tuple[Verification, ...]:
    """The project's `verification.jsonl` rows in file order. The ledger holds
    pointers and reason codes only, so the rows pass through typed."""
    bucket = _bucket(project)
    if bucket is None:
        return ()
    return tuple(
        Verification(_text(row.get("ts")), _text(row.get("item_ref")),
                     _text(row.get("check")), _text(row.get("reason")))
        for row in jsonl.read(bucket / "verification.jsonl").rows
        if isinstance(row, dict))


def team(project, *, live: bool) -> tuple:
    """`((author, Opened), ...)` for the teammates' newest checkpoints
    (`store.read_team`, which has already applied the inbound gate)."""
    snap = snapshot(project)
    return tuple((author, _opened(raw, snap, live))
                 for author, raw in store.read_team(project_dir=project))


def _occurrence(raw: dict) -> tuple:
    return (raw.get("session_id"), raw.get("created"))


_FIELD_BY_KIND = {f.kind: f for f in schema.ITEM_FIELDS}


def _field_for(kind) -> schema.ItemField:
    """The field of an index row's kind. A kind the table does not know is
    judged by value alone (no quarantine is scoped to it), never skipped."""
    got = _FIELD_BY_KIND.get(kind)
    return got if got is not None else schema.ItemField(
        "", "", False, str(kind), None, False, False)


def _iso(epoch) -> str | None:
    """The `created` stamp of an index row's epoch, in the checkpoint format."""
    try:
        return datetime.fromtimestamp(
            float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch(created) -> float:
    """Sort key of a `created` stamp: its epoch, or 0.0 when it has none (an
    unstamped copy is the oldest)."""
    return store._created_epoch(created) or 0.0


def _meta_of(checkpoint: dict) -> store.Meta:
    return store.Meta(*(checkpoint.get(n) for n in store.Meta._fields))


def _holds(opened: Opened, wanted) -> dict:
    """What one opened checkpoint holds of `wanted` ids: `(field, item)` for a
    copy the reader may see, the `Withheld` for one it may not. A visible copy
    comes first when an id is held twice."""
    out: dict = {}
    if isinstance(opened.checkpoint, dict):
        for fld, item in schema.iter_items(opened.checkpoint):
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id in wanted \
                    and item_id not in out:
                out[item_id] = (fld, item)
    for verdict in opened.withheld:
        if verdict.item_id in wanted and verdict.item_id not in out:
            out[verdict.item_id] = verdict
    return out


def _lookup_withheld(verdict: Withheld, item_id: str, notes: tuple = ()) -> Withheld:
    """A `Withheld` as a lookup hands it out. A forgotten value's key never
    travels (scar 0119): it names the value it tombstones."""
    return Withheld(item_id, verdict.kind, verdict.reason,
                    verdict.quarantine_id,
                    "" if verdict.reason == "forgotten" else verdict.value_key,
                    notes, verdict.receipt)


def _session_state(session_id, slug) -> str:
    """What an index row's session file is to this project, for a row the
    session-file reader did not open: `missing` (no local file under that
    name: a teammate's mirrored checkpoint, H12), `stampless` (a local file
    with no project stamp: a legacy file recall attributed through the bucket
    pointer, H12), `torn` (a local file that does not parse), `foreign` (a
    file stamped for another project). Only the first two are answered from
    the row; the last two name nothing of the value."""
    if not (isinstance(session_id, str) and session_id
            and session_id == store._safe_name(session_id)):
        return "missing"
    if not (config.checkpoint_dir() / f"{session_id}.json").is_file():
        return "missing"
    raw = store.read_checkpoint(session_id)
    if not isinstance(raw, dict):
        return "torn"
    stamp = raw.get("project_slug")
    if not stamp:
        return "stampless"
    return "foreign" if stamp != slug else "missing"


_INDEX_ONLY = {"missing": "team-mirror", "stampless": "pointer-attributed-legacy"}
_ROW_NOTES = {"torn": "unreadable", "foreign": "foreign-stamp"}


def _answer_from_row(row: dict, item_id: str, snap: Snapshot,
                     occurrences: tuple, state: str) -> Found | Withheld:
    """An index row whose file the view cannot open, classified at read time
    like any other copy (H12): the row is never handed out as it stands."""
    fld = _field_for(row.get("kind"))
    item = {"id": item_id, "text": row.get("text"), "trust": row.get("trust"),
            "quote": row.get("quote") or None,
            "origin_session": row.get("session_id"),
            "project_slug": row.get("project_slug")}
    verdict = classify(fld, item, snap)
    if isinstance(verdict, Withheld):
        return _lookup_withheld(verdict, item_id)
    meta = store.Meta(
        row.get("session_id"), _iso(row.get("created")), row.get("author"),
        None, row.get("project_slug"), None, None, None, None, None, None)
    return Found(item, fld, occurrences, meta, "index",
                 (f"index_only:{_INDEX_ONLY[state]}",))


def lookup_many(project, ids, *, snap: Snapshot | None = None,
                window: tuple | None = None
                ) -> dict[str, Found | Withheld | Absent]:
    """Exact-id read of this project's own items, in four tiers, bounded by
    the bucket and never by the machine.

    1. The pointer window (`pointers`, bucket-local, DAIMON_CHECKPOINT_HISTORY
       deep): a visible copy is found by walking the opened body, a withheld
       one in `Opened.withheld`. The newest `created` epoch wins; a copy with
       no usable stamp is the oldest.
    2. The index locator (`index_locate`, read-only, never refreshed): the
       ids tier 1 did not find are located, their session files opened in one
       `open_sessions` call and each id classified from the newest file that
       still holds it (a stale row is skipped). A row whose file is not local
       or carries no slug stamp is answered from the row (`source="index"`),
       classified at read time.
    3. Hints: `snap.forgotten_ids` (a tombstone that still stands) and
       `snap.quarantine_items` (an active quarantine that named the id).
    4. `Absent`.

    A closed snapshot (the trust ledger cannot be read) skips tiers 2 and 3
    and says so in `Absent.notes`. `snap` defaults to the bucket's light
    judge snapshot; `window` is a pointer window the caller already holds.
    Every asked id is a key of the result, once."""
    wanted = list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))
    if not wanted:
        return {}
    slug = store.project_slug(config.resolve_project_dir(project))
    snap = snap if snap is not None else judge(slug).snap
    window = (window if window is not None
              else pointers(project, snap=snap))
    notes: tuple = ("pointer_unreadable",) if any(
        not p.readable for p in window) else ()
    want = frozenset(wanted)

    # tier 1: the window. id -> [(epoch, order, session id, created, hold)]
    seen: dict[str, list] = {}
    for order, ptr in enumerate(window):
        if not ptr.readable or ptr.meta is None:
            continue
        stamp = (ptr.opened.checkpoint or {}).get("created", ptr.meta.created)
        for item_id, hold in _holds(ptr.opened, want).items():
            seen.setdefault(item_id, []).append(
                (_epoch(stamp), order, ptr.meta.session_id or ptr.ref, stamp,
                 hold, ptr))
    out: dict = {}
    for item_id, copies in seen.items():
        copies.sort(key=lambda c: (-c[0], c[1]))
        best = copies[0]
        if isinstance(best[4], Withheld):
            out[item_id] = _lookup_withheld(best[4], item_id)
            continue
        distinct: dict = {}
        for epoch, _o, sid, created, _h, _p in copies:
            distinct.setdefault(sid, (epoch, created))
        fld, item = best[4]
        out[item_id] = Found(
            item, fld, tuple((sid, c) for sid, (_e, c) in distinct.items()),
            best[5].meta, "pointer")

    # tier 2: the locator, once for the batch
    located: dict = {}
    if not snap.closed:
        got = index_locate.locate(config.recall_db(), slug or "", wanted)
        located, notes_index = got.rows, got.notes
        notes = notes + notes_index
        for item_id, hit in list(out.items()):
            if not isinstance(hit, Found):
                continue
            known = {sid for sid, _c in hit.occurrences}
            extra = []
            for row in located.get(item_id, ()):
                sid = row.get("session_id")
                if sid and sid not in known:
                    known.add(sid)
                    extra.append((sid, _iso(row.get("created"))))
            if extra:
                out[item_id] = dataclasses.replace(
                    hit, occurrences=tuple(sorted(
                        (*hit.occurrences, *extra),
                        key=lambda o: -_epoch(o[1]))))
    unresolved = [i for i in wanted if i not in out]
    todo = [i for i in unresolved if i in located]
    opened = {}
    if todo:
        sids = list(dict.fromkeys(
            r["session_id"] for i in todo for r in located[i]
            if r.get("session_id")))
        opened = open_sessions(project, sids, live=False, snap=snap)
    row_notes: dict[str, tuple] = {}
    for item_id in todo:
        rows = located[item_id]
        verified = []
        answer = None
        for row in rows:
            sid = row.get("session_id")
            file = opened.get(sid)
            if file is not None:
                hold = _holds(file, frozenset((item_id,))).get(item_id)
                if hold is None:
                    continue                     # stale: the file lost it
                verified.append((sid, (file.checkpoint or {}).get("created")))
                if answer is None:
                    answer = ("file", hold, file)
                continue
            state = _session_state(sid, slug)
            if state in _ROW_NOTES:
                # a torn file or another project's: nothing of the row is used
                row_notes[item_id] = (*row_notes.get(item_id, ()),
                                      _ROW_NOTES[state])
                continue
            verified.append((sid, _iso(row.get("created"))))
            if answer is None:
                answer = ("row", row, state)
        if answer is None:
            continue
        kind, hold, file = answer
        if kind == "row":
            out[item_id] = _answer_from_row(hold, item_id, snap,
                                            tuple(verified), file)
        elif isinstance(hold, Withheld):
            out[item_id] = _lookup_withheld(hold, item_id)
        else:
            fld, item = hold
            out[item_id] = Found(item, fld, tuple(verified),
                                 _meta_of(file.checkpoint or {}), "session")

    # tiers 3 and 4: what is left
    if snap.closed:
        notes = notes + ("index_closed",)
    for item_id in wanted:
        if item_id in out:
            continue
        if not snap.closed:
            if item_id in snap.forgotten_ids:
                out[item_id] = Withheld(item_id, "unknown", "forgotten", None,
                                        "", notes + row_notes.get(item_id, ()))
                continue
            hint = snap.quarantine_items.get(item_id)
            if hint is not None:
                kind, quarantine_id, value_key = hint
                out[item_id] = Withheld(item_id, kind, "quarantine",
                                        quarantine_id, value_key,
                                        notes + row_notes.get(item_id, ()))
                continue
        # Nothing holds the id. A quarantine of its value that no record tied
        # to this id cannot be seen from here, so the answer says where it
        # looked: the window and the index.
        out[item_id] = Absent(notes + row_notes.get(item_id, ())
                              + (() if snap.closed else ("outside_window",)))
    return {i: out[i] for i in wanted}


def lookup(project, item_id: str) -> Found | Withheld | Absent:
    """The item with this id: `lookup_many` of one."""
    return lookup_many(project, [item_id])[item_id]


@dataclass(frozen=True)
class Appearance:
    """One pointer generation that holds an item. `index` is the number of
    writes back (0 is `latest`), `ref` the pointer's name and `pointer_file`
    its file. `verdict` is `Visible` (the copy as that generation held it) or
    `Withheld`; `trust` and `carried_from` are the visible copy's and None for
    a withheld one, which never says "stated here"."""

    index: int
    ref: str
    pointer_file: str
    session_id: str | None
    created: str | None
    verdict: Visible | Withheld
    trust: str | None
    carried_from: str | None


@dataclass(frozen=True)
class Lineage:
    """How one item got here, over one snapshot. `appearances` are the
    readable pointers that hold it, newest first; `events` its judged
    lifecycle rows in the fold's order; `lifecycle` one of `active`,
    `resolved`, `superseded`, `forgotten`; `verdict` the `lookup_many` answer
    (it can name a copy older than the pointer window). `refs` lists every
    pointer file of the window newest first, `unreadable` the torn ones and
    `sessions` the sessions the readable ones belong to. `latest` is the
    judged event the lifecycle word came from (None for an id with none),
    `corroboration` the snapshot's fold for the id and `snapshot` the one
    snapshot everything above was judged by."""

    appearances: tuple
    events: tuple
    lifecycle: str
    verdict: Found | Withheld | Absent
    refs: tuple = ()
    unreadable: tuple = ()
    sessions: frozenset = frozenset()
    latest: Event | None = None
    corroboration: Mapping = field(default_factory=dict)
    snapshot: Snapshot | None = None


def lifecycle_word(event) -> str:
    """The lifecycle word of the latest event of a ref: `forgotten` for a
    tombstone that stands (the event is the latest, so a later reopen has
    already replaced it), `superseded` for a `superseded-by:` status,
    `resolved` for any other closing status, else `active`."""
    if not isinstance(event, dict):
        return "active"
    if store.is_tombstone_status(event.get("status")):
        return "forgotten"
    if str(event.get("status") or "").strip().lower().startswith(
            "superseded-by:"):
        return "superseded"
    return "resolved" if store.is_resolved(event) else "active"


def _pointer_index(ref: str) -> int:
    return 0 if ref == "latest" else int(ref.split("-")[1])


def lineage(project, item_id: str) -> Lineage:
    """Everything the history verbs say about one id, from ONE full snapshot
    and ONE pointer window shared with `lookup_many` and `item_events`."""
    snap = snapshot(project)
    window = pointers(project, snap=snap)
    verdict = lookup_many(project, [item_id], snap=snap,
                          window=window)[item_id]
    appearances = []
    for ptr in window:
        if not ptr.readable or ptr.meta is None:
            continue
        hold = _holds(ptr.opened, frozenset((item_id,))).get(item_id)
        if hold is None:
            continue
        if isinstance(hold, Withheld):
            appearances.append(Appearance(
                _pointer_index(ptr.ref), ptr.ref, f"{ptr.ref}.json",
                ptr.meta.session_id, ptr.meta.created,
                _lookup_withheld(hold, item_id), None, None))
            continue
        _fld, item = hold
        appearances.append(Appearance(
            _pointer_index(ptr.ref), ptr.ref, f"{ptr.ref}.json",
            ptr.meta.session_id, ptr.meta.created, Visible(item),
            item.get("trust"), item.get("carried_from") or None))
    return Lineage(
        tuple(appearances), item_events(project, item_id, snap=snap),
        lifecycle_word(snap.resolutions.get(item_id)), verdict,
        tuple(p.ref for p in window),
        tuple(p.ref for p in window if not p.readable),
        frozenset(p.meta.session_id for p in window
                  if p.readable and p.meta and p.meta.session_id),
        latest_event(snap, item_id),
        snap.corroborations.get(item_id, {}), snap)


def source_ref(session_id) -> dict | None:
    """The non-item `source_ref` envelope key of the named session file, or
    None when the file is missing, torn or carries none. It names where the
    transcript lives and holds no item, so it needs no judgement."""
    raw = store.read_checkpoint(session_id) if isinstance(
        session_id, str) and session_id else None
    ref = raw.get("source_ref") if isinstance(raw, dict) else None
    return ref if isinstance(ref, dict) else None


_MATCH_HOW = ("terms", "id", "substring")


def match(project, query: str, *, how: str = "terms") -> Match:
    """The candidates a binding verb would bind a query to, in the live OWN
    body (scar 0063). `how="terms"`: an exact id, else every id-bearing item
    `carry._same_item` says is the same as the query (terms common to all of
    the checkpoint's texts ignored). `how="id"`: the exact id only.
    `how="substring"`: case-insensitive containment over the visible items,
    the topic singleton and id-less dicts included; a withheld match is not
    looked at, so `withheld` is always 0 (a substring plus a count is a
    character oracle).

    A forgotten candidate is dropped and counted nowhere. `withheld` counts
    quarantine and closed candidates; `exact` carries the one withheld
    candidate an exact id named (no value, no forgotten key)."""
    if how not in _MATCH_HOW:
        raise ValueError(f"unknown match mode: {how!r}")
    slug = store.project_slug(config.resolve_project_dir(project))
    snap = judge(slug).snap
    raw = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    if not isinstance(raw, dict):
        return Match((), 0, None, snap.closed)
    if how == "substring":
        needle = str(query).lower()
        hits = []
        for fld, value in schema.iter_fields(raw):
            entries = ([(None, value)] if fld.singleton
                       else list(enumerate(value)) if isinstance(value, list)
                       else [])
            for index, item in entries:
                if (isinstance(item, dict)
                        and needle in str(item.get("text", "")).lower()
                        and isinstance(classify(fld, item, snap), Visible)):
                    hits.append(Found(item, fld, (_occurrence(raw),),
                                      at=(fld.section, fld.key, index)))
        return Match(tuple(hits), 0, None, snap.closed)
    items = [(fld, item) for fld, item in schema.iter_items(raw)
             if not fld.singleton and item.get("id")]
    exact = [(fld, item) for fld, item in items if item["id"] == query]
    if exact:
        candidates = exact[:1]
    elif how == "id":
        candidates = []
    else:
        generic = carry._generic_terms(
            [str(item.get("text") or "") for _fld, item in items])
        candidates = [(fld, item) for fld, item in items
                      if carry._same_item(query, str(item.get("text") or ""),
                                          generic)]
    hits = []
    withheld = 0
    named = None
    for fld, item in candidates:
        verdict = classify(fld, item, snap)
        if isinstance(verdict, Withheld):
            if verdict.reason == "forgotten":
                continue
            withheld += 1
            if exact:
                named = _lookup_withheld(verdict, str(item["id"]))
        else:
            hits.append(Found(item, fld, (_occurrence(raw),)))
    return Match(tuple(hits), withheld, named, snap.closed)


@dataclass(frozen=True)
class RelationView:
    """Relation edges a reader may see, with the text of their endpoints.
    `texts` maps an item id to its visible text or to the withheld marker
    (a quarantined or closed endpoint); an endpoint nothing holds is absent
    from it (the renderers print `[unresolved]`). `withheld` is 0: an edge
    that touches a forgotten item is dropped and counted nowhere."""

    rows: tuple
    texts: Mapping
    withheld: int = 0


def relations(project, *, states=None, item_id=None,
              relation_id=None) -> RelationView:
    """The relation edges of a project joined with their endpoint text, the
    one place the ledger (which holds no text) meets the checkpoints.

    The rows come from the relations ledger: every record in `states`, or the
    confirmed edges touching `item_id`, or the one record `relation_id`. The
    endpoint ids of those rows go through `lookup_many` ONCE (one light
    snapshot, one pointer window, chunked index seeks), so the join is bounded
    by the edge count and never walks the machine. An edge with a forgotten
    endpoint is dropped silently (H7): the list never says an item was
    forgotten. A quarantined or closed endpoint keeps its edge and reads as
    the marker."""
    if relation_id is not None:
        record = relation_ledger.get(relation_id, project_dir=project)
        rows = [record] if record is not None else []
    elif item_id is not None:
        rows = relation_ledger.for_item(item_id, project_dir=project)
    else:
        rows = relation_ledger.listing(states=states, project_dir=project)
    ids = list(dict.fromkeys(
        i for row in rows for i in sorted(relation_ledger.row_item_ids(row))))
    found = lookup_many(project, ids) if ids else {}
    erased = {i for i, v in found.items()
              if isinstance(v, Withheld) and v.reason == "forgotten"}
    kept = tuple(row for row in rows
                 if not relation_ledger.row_item_ids(row) & erased)
    named = {i for row in kept for i in relation_ledger.row_item_ids(row)}
    texts: dict[str, str] = {}
    for i, v in found.items():
        if i not in named:
            continue
        if isinstance(v, Found):
            texts[i] = str(v.item.get("text") or "")
        elif isinstance(v, Withheld):
            texts[i] = display.withheld_marker(v)
    return RelationView(kept, texts, 0)


def suppressed(project, now: float) -> Suppression:
    """The project's own suppressed items, for `status --suppressed`. Built on
    `open(live=False)`: what `open` withholds is the quarantined list (and the
    `closed` count), what it keeps and `closing_event` closes is the resolved
    list, and the #14 candidates come from `briefing.stamp` over the opened
    checkpoint, so a withheld item is never one. A forgotten value is dropped
    by `open` and reported nowhere. `briefing` is imported inside the
    function, as in `Snapshot.fuzzy_events`."""
    from . import briefing
    opened = open(project, live=False, route=store.Route.OWN)
    snap = opened.snapshot
    resolved = []
    candidates: tuple = ()
    if opened.checkpoint is not None:
        for fld, item in schema.iter_items(opened.checkpoint):
            event = None if fld.singleton else closing_event(item, snap)
            if event is not None:
                resolved.append(Resolved(item, fld, event))
        candidates = tuple(
            briefing.stamp(opened.checkpoint, snap, now, with_stale=False)[1])
    return Suppression(
        tuple(resolved),
        tuple(w for w in opened.withheld if w.reason == "quarantine"),
        sum(w.reason == "closed" for w in opened.withheld),
        candidates, snap.notes())
