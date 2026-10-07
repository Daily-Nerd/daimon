"""The viewer's reads. Every function answers for a project slug through
`daimon_briefing.view`, so a value a reader may not see never leaves it: the
files, the folds and the withhold decisions are the view's. The store a read
answers for is the one the server's runner set for the request
(`config.checkpoint_dir_override`); nothing here names a directory.
"""
import re

from daimon_briefing import api, schema, view

POINTER_RE = re.compile(r"^(latest|prev-[1-9][0-9]?)$")
ITEM_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{6,40}(-\d+)?$")

def _receipts_gate(slug: str) -> bool:
    """Has this project opted into receipts? Any pointer of its window says so,
    so a project that never enabled them shows nothing at all rather than being
    nagged about a feature it declined."""
    return any(p.meta is not None and p.meta.receipts is True
               for p in view.pointers(slug))

def list_buckets(own: str | None) -> list[dict]:
    """The sidebar: every project bucket the caller may list (`view.projects`,
    the tenant rule included), newest first, a torn pointer last with its
    fields None. `active_topic` and `item_count` are what a reader may see: a
    withheld topic is None and withheld items are not counted."""
    out = [{"slug": b.slug,
            "project_name": b.name if isinstance(b.name, str) and b.name else None,
            "created": b.created if isinstance(b.created, str) else None,
            "active_topic": b.peek.topic,
            "item_count": b.peek.visible_items if b.readable else None}
           for b in view.projects(own)]
    out.sort(key=lambda b: b["created"] or "", reverse=True)  # "" sorts lowest, so None lands last
    return out

def list_recent(slug: str) -> dict:
    """The sidebar window of one project: its pointers (ref, created, visible
    topic; a torn pointer keeps its entry with None fields), the number of
    session files behind them and the ledger-health notes."""
    window = view.pointers(slug)
    listing = view.sessions(slug)
    checkpoints = []
    for p in window:
        cp = p.opened.checkpoint or {}
        topic = (cp.get("working_context") or {}).get("active_topic")
        checkpoints.append({
            "ref": p.ref, "created": p.meta.created if p.meta else None,
            "active_topic": topic.get("text") if isinstance(topic, dict) else None})
    return {"checkpoints": checkpoints, "sessions_total": len(listing.rows),
            "notes": list(listing.notes)}

KNOWN_FORMAT = "D-019"

def _norm_str(v):
    return v if isinstance(v, str) and v else None

def _norm_str_list(v):
    if not isinstance(v, list):
        return None
    return [x for x in v if isinstance(x, str)]

def _norm_provenance(raw):
    """Normalize a quote_provenance receipt to the subset the panel renders.
    Each field individually None when absent/wrong-shaped; whole value None
    when raw is not a dict — absence must read as absence downstream."""
    if not isinstance(raw, dict):
        return None
    digest = raw.get("digest") if isinstance(raw.get("digest"), dict) else {}
    binding = raw.get("binding") if isinstance(raw.get("binding"), dict) else {}
    # The producer writes verifier as an OBJECT (provenance.py:188, {id, version}).
    # A string normalizer nulled every one of them, so the field naming WHO checked
    # the quote rendered as "not recorded" on every provenanced item. Bare strings
    # stay readable: older receipts recorded the id alone.
    verifier = raw.get("verifier")
    if isinstance(verifier, dict):
        verifier_id = _norm_str(verifier.get("id"))
        version = verifier.get("version")
        verifier_version = (version if isinstance(version, int)
                            and not isinstance(version, bool) else None)
    else:
        verifier_id, verifier_version = _norm_str(verifier), None
    # D-019 (#829): the stitching span verdict — the field that defines the
    # format bump — passes through per-key, unknown-shaped values reading as
    # None rather than a guessed verdict.
    stitching = raw.get("stitching")
    if isinstance(stitching, dict):
        cm = stitching.get("cross_message")
        cr = stitching.get("cross_role")
        stitching_norm = {
            "cross_message": cm if isinstance(cm, bool) else None,
            "cross_role": cr if isinstance(cr, bool) else None,
        }
    else:
        stitching_norm = None
    return {
        "verifier": verifier_id,
        "verifier_version": verifier_version,
        "outcome": _norm_str(raw.get("outcome")),
        "checked_at": _norm_str(raw.get("checked_at")),
        "digest_algorithm": _norm_str(digest.get("algorithm")),
        "message_ids": _norm_str_list(binding.get("message_ids")),
        "stitching": stitching_norm,
    }

def _norm_item(raw):
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    trust = raw.get("trust")
    qv = raw.get("quote_verified")
    imp = raw.get("importance")
    return {
        "text": str(raw.get("text", "")),
        "trust": trust if trust in ("verbatim", "inferred") else None,
        "carried_from": raw.get("carried_from") or None,
        "quote": raw.get("quote") or None,
        "quote_verified": qv if isinstance(qv, bool) else None,
        "because": raw.get("because") or None,
        "id": raw.get("id") or None,
        "external_state": bool(raw.get("external_state")),
        # 1-10, matching the producer (serializer.py:151 instructs the range,
        # :678 clamps to it). A 1-5 bound nulled 89% of rated items, and because
        # the distribution is right-skewed it deleted precisely the load-bearing
        # half, inverting every importance-sorted list.
        "importance": imp if isinstance(imp, int) and not isinstance(imp, bool) and 1 <= imp <= 10 else None,
        "last_verified": _norm_str(raw.get("last_verified")),
        "origin_session": _norm_str(raw.get("origin_session")),
        "source_message_ids": _norm_str_list(raw.get("source_message_ids")),
        "quote_provenance": _norm_provenance(raw.get("quote_provenance")),
    }


# The checkpoint fields the page sections draw from. The sections themselves
# (their keys, labels and order) are the page's; the field and its kind word
# are `schema.ITEM_FIELDS`'s.
_FIELD = {f.key: f for f in schema.ITEM_FIELDS}

def _section(key, label, field, items):
    return {"key": key, "label": label, "kind": field.kind, "items": items}

def _items(data, field, partial):
    block = data.get(field.section)
    raw = block.get(field.key) if isinstance(block, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        partial.append(f"Section '{field.key}' has an unexpected shape and was skipped.")
        return []
    return [i for i in (_norm_item(r) for r in raw) if i is not None]

def _normalize(data):
    """Turn a checkpoint the view returned into (meta, sections, partial).
    Shared by load_checkpoint (pointer-based), diff_checkpoints and the walk
    (session files). The body is already judged: a withheld item is not in it,
    so nothing here decides what a reader may see."""
    partial = []
    fv = data.get("format_version")
    if fv != KNOWN_FORMAT:
        partial.append(f"Checkpoint uses schema {fv or 'unknown'}; this inspector understands {KNOWN_FORMAT}. Showing what's readable.")

    topic_field = _FIELD["active_topic"]
    block = data.get(topic_field.section)
    topic = block.get(topic_field.key) if isinstance(block, dict) else None
    topic = topic if isinstance(topic, dict) else {}

    asks = _FIELD["open_questions"]
    questions = _items(data, asks, partial)
    sections = [
        _section("verify_first", "Verify before trusting", asks,
                 [i for i in questions if i["external_state"]]),
        _section("decisions", "Decisions", _FIELD["recent_decisions"],
                 _items(data, _FIELD["recent_decisions"], partial)),
        _section("open_loops", "Open loops", asks,
                 [i for i in questions if not i["external_state"]]),
        _section("beliefs", "Beliefs", _FIELD["strong_beliefs"],
                 _items(data, _FIELD["strong_beliefs"], partial)),
        _section("uncertainties", "Uncertainties", _FIELD["uncertainties"],
                 _items(data, _FIELD["uncertainties"], partial)),
        _section("contradictions", "Contradictions",
                 _FIELD["contradictions_flagged"],
                 _items(data, _FIELD["contradictions_flagged"], partial)),
    ]
    meta = {
        "created": data.get("created"),
        "author": data.get("author"),
        "format_version": fv,
        "session_id": data.get("session_id"),
        "active_topic": topic.get("text"),
    }
    return meta, sections, partial

def load_checkpoint(slug: str, ref: str):
    if not POINTER_RE.fullmatch(ref or ""):
        return {"ok": False, "error": {
            "what": f"Checkpoint reference {ref!r} isn't one this inspector serves.",
            "why": "Only 'latest' and 'prev-N' pointers are served.",
            "fix": "Pick a checkpoint from the sidebar.",
        }}
    window = view.pointers(slug)
    mine = next((p for p in window if p.ref == ref), None)
    if mine is None:
        return {"ok": False, "error": {
            "what": f"Checkpoint {ref} doesn't exist.",
            "why": "The pointer chain is shorter than requested.",
            "fix": "Pick a checkpoint from the sidebar.",
        }}
    if not mine.readable:
        return {"ok": False, "error": {
            "what": f"Couldn't read checkpoint {ref}.",
            "why": "The file isn't complete JSON — possibly a partial write.",
            "fix": "Re-run `daimon heal`, or pick another checkpoint from the sidebar.",
        }}

    body = mine.opened.checkpoint or {}
    meta, sections, partial = _normalize(body)
    # Has this project opted into receipts? Any pointer of the window says so.
    if any(p.meta is not None and p.meta.receipts is True for p in window):
        meta["receipt"] = api.receipt_state(body)
    return {"ok": True, "partial": partial, "sections": sections, "meta": meta,
            "notes": list(mine.opened.snapshot.notes())}

def history(slug: str) -> dict:
    """The session list of one project through the view: the file stem as the
    session id, `created`, the topic a reader may see, the count of session
    files that could not be read and the ledger-health notes."""
    listing = view.sessions(slug)
    return {"sessions": [{"session_id": r.session_id, "created": r.created,
                          "active_topic": r.topic} for r in listing.rows],
            "unreadable": listing.unreadable, "notes": list(listing.notes)}

def _resolution_note(event, snap):
    """The note of a resolution event as a reader may see it: a quarantined
    value reads as its marker, as on every host, a forgotten one as absent.
    Notes are human prose, so an unreadable trust ledger does not mask them."""
    note = event.get("note")
    verdict = view.prose_verdict(note, snap, closed_masks=False)
    if verdict is None:
        return note
    return None if verdict.reason == "forgotten" else api.withheld_marker(verdict)

def _activity_event_row(ev):
    kind = ev.kind or "unknown"
    if kind == "corroboration":
        detail = ev.item_text or ev.note or ev.status
    elif kind == "handoff":
        detail = ev.note
    else:
        detail = ev.note or ev.item_text
    return {"ts": ev.ts, "kind": kind, "session_id": None,
            "item_ref": ev.item_ref, "detail": detail,
            "extra": {"status": ev.status, "source": ev.source,
                       "item_text": ev.item_text}}

def _activity_check_row(row):
    detail = (f"{row.check}: {row.reason}" if row.check and row.reason
              else (row.reason or row.check))
    return {"ts": row.ts, "kind": "quote_check", "session_id": None,
            "item_ref": row.item_ref, "detail": detail,
            "extra": {"check": row.check, "reason": row.reason}}

def project_activity(slug: str) -> dict:
    """One chronological feed per project: session markers + events.jsonl +
    verification.jsonl, newest -> oldest. Every row is read through the view:
    a session's topic and an event's note, item text and status are what a
    reader may see, and a forgotten value or the key that names it is in no
    row."""
    listing = view.sessions(slug)
    rows = [{"ts": _norm_str(s.created), "kind": "session",
             "session_id": s.session_id, "item_ref": None,
             "detail": _norm_str(s.topic),
             "extra": {"topic": _norm_str(s.topic)}} for s in listing.rows]
    rows.extend(_activity_event_row(ev) for ev in view.events(slug))
    rows.extend(_activity_check_row(v) for v in view.verifications(slug))
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    partial = []
    if listing.unreadable:
        partial.append(f"{listing.unreadable} session file(s) couldn't be read and are missing from the feed.")
    return {"ok": True, "rows": rows, "partial": partial,
            "notes": list(listing.notes)}

def diff_checkpoints(slug: str, sid_a: str, sid_b: str) -> dict:
    """A=older, B=newer. sid_a/sid_b must exact-match a session id from the
    project's session list, checked before any session is opened. Both bodies
    come through the view, so a withheld item is in neither; an item that left
    and was closed by a resolution (any resolving status: `snapshot.resolved_refs`)
    is `resolved`, else `gone`."""
    valid_ids = {r.session_id for r in view.sessions(slug).rows}
    for sid in (sid_a, sid_b):
        if sid not in valid_ids:
            return {"ok": False, "error": {
                "what": f"Session {sid!r} isn't part of {slug}'s history.",
                "why": "The session id doesn't match any recorded checkpoint.",
                "fix": "Pick a session from the project's history list.",
            }}
    opened = view.open_sessions(slug, (sid_a, sid_b), live=False)
    for sid in (sid_a, sid_b):
        if sid not in opened:
            return {"ok": False, "error": {
                "what": f"Session {sid} doesn't exist.",
                "why": "No checkpoint file was found for that session.",
                "fix": "Pick a session from the project's history.",
            }}
    snap = opened[sid_a].snapshot
    meta_a, sections_a, partial_a = _normalize(opened[sid_a].checkpoint)
    meta_b, sections_b, partial_b = _normalize(opened[sid_b].checkpoint)

    def index(sections):
        by_id, skipped = {}, 0
        for sec in sections:
            for item in sec["items"]:
                iid = item.get("id")
                if not iid:
                    skipped += 1
                    continue
                by_id[iid] = dict(item, section=sec["key"])
        return by_id, skipped

    map_a, skipped_a = index(sections_a)
    map_b, skipped_b = index(sections_b)
    ids_a, ids_b = set(map_a), set(map_b)

    born = [map_b[i] for i in sorted(ids_b - ids_a)]
    resolved, gone = [], []
    for iid in sorted(ids_a - ids_b):
        item = map_a[iid]
        if iid in snap.resolved_refs:
            ev = snap.resolutions[iid]
            resolved.append({"item": item, "note": _resolution_note(ev, snap),
                             "ts": ev.get("ts")})
        else:
            gone.append(item)

    # One changed list for every tracked field, values included: the diff view
    # renders old struck through above new, so field names alone are not
    # enough. Trust transitions are changed rows like any other — the frozen
    # vocabulary rides "changed" (§8), nothing gets its own bucket.
    carried, changed = [], []
    for iid in sorted(ids_a & ids_b):
        item_a, item_b = map_a[iid], map_b[iid]
        fields = [{"field": f, "from": item_a.get(f), "to": item_b.get(f)}
                  for f in _BIO_TRACKED_FIELDS if item_a.get(f) != item_b.get(f)]
        if fields:
            changed.append({"item": item_b, "fields": fields})
        else:
            carried.append({"item": item_b})

    partial = list(partial_a) + list(partial_b)
    skipped_total = skipped_a + skipped_b
    if skipped_total:
        partial.append(f"Skipped {skipped_total} item(s) without an id during diff.")

    return {
        "ok": True,
        "a": meta_a, "b": meta_b,
        "born": born, "resolved": resolved, "gone": gone,
        "carried": carried, "changed": changed,
        "partial": partial, "notes": list(snap.notes()),
    }

def _open_all(slug: str):
    """`(listing, opened, snap)`: the project's session listing, every listed
    session through the view (`{session_id: Opened}`, one snapshot) and that
    snapshot. A listed session that vanished before it was opened is absent."""
    listing = view.sessions(slug)
    opened = view.open_sessions(
        slug, [r.session_id for r in listing.rows], live=False)
    snap = (next(iter(opened.values())).snapshot if opened
            else view.snapshot(slug))
    return listing, opened, snap

def _walk_transitions(slug: str):
    """Pairwise walk over every session, oldest -> newest, emitting one event per
    recorded transition: first_seen (item appears), changed (a tracked field
    differs from the previous sighting), last_seen (item present before, absent
    now — ts is the created of its FINAL sighting, the session named is the one
    whose absence recorded it). Carried, unchanged sightings emit nothing:
    a carry is not an event, and counting it would let agreement pose as
    corroboration. Every event carries the trust class the item held AT that
    session — the strip draws marks with the class of the time, not today's.
    Shared by project_ledger, session_events and project_grid so the
    surfaces can never disagree about what happened."""
    listing, opened, snap = _open_all(slug)
    events: list[dict] = []
    latest_item: dict[str, dict] = {}
    last_sight: dict[str, object] = {}
    prev_ids, gone = None, set()
    for s in reversed(listing.rows):  # oldest -> newest
        op = opened.get(s.session_id)
        if op is None:
            continue  # vanished between listing and opening: skip, don't abort the walk
        _, sections, _ = _normalize(op.checkpoint)
        cur = _index_items(sections)
        for iid, item in cur.items():
            if iid not in latest_item or iid in gone:
                gone.discard(iid)
                events.append({"item_id": iid, "kind": "first_seen", "ts": s.created,
                               "session_id": s.session_id, "detail": None,
                               "trust": item.get("trust")})
            else:
                changed = [f for f in _BIO_TRACKED_FIELDS
                           if item.get(f) != latest_item[iid].get(f)]
                if changed:
                    events.append({"item_id": iid, "kind": "changed", "ts": s.created,
                                   "session_id": s.session_id,
                                   "detail": ", ".join(changed),
                                   "trust": item.get("trust")})
            latest_item[iid] = item
        if prev_ids is not None:
            for iid in sorted(prev_ids - set(cur)):
                gone.add(iid)
                events.append({"item_id": iid, "kind": "last_seen",
                               "ts": last_sight.get(iid),
                               "session_id": s.session_id, "detail": None,
                               "trust": latest_item[iid].get("trust")})
        for iid in cur:
            last_sight[iid] = s.created
        prev_ids = set(cur)
    return {"events": events, "items": latest_item,
            "unreadable": listing.unreadable, "sessions": listing.rows,
            "opened": opened, "snap": snap, "notes": list(snap.notes())}

# Row order inside a ledger group and a session page: births, then changes,
# then departures — the order the header counts read in — id-tiebroken so the
# output never depends on dict insertion.
_TRANSITION_ORDER = {"first_seen": 0, "changed": 1, "last_seen": 2}

def _ledger_partial(unreadable):
    if not unreadable:
        return []
    return [f"{unreadable} session file(s) couldn't be read and are missing from the ledger."]

def project_ledger(slug: str) -> dict:
    """The ledger screen: one row per object, grouped under the session of its
    latest recorded transition. A later resolution or quote check (own ts, no
    session attribution in their ledgers) can overtake the LAST EVENT field,
    but never moves the row to another group — the viewer must not invent the
    attribution the stored rows don't carry. Resolutions are the kernel fold
    (`snapshot.resolved_refs`), the same rule `daimon diff` applies."""
    walk = _walk_transitions(slug)
    snap = walk["snap"]
    hist_sessions = walk["sessions"]  # newest -> oldest
    head = None
    if hist_sessions:
        head = {"session_id": hist_sessions[0].session_id,
                "created": hist_sessions[0].created}

    ver_rows = view.verifications(slug)
    latest_check: dict[str, str] = {}
    for row in ver_rows:
        if row.item_ref and (row.ts or "") > latest_check.get(row.item_ref, ""):
            latest_check[row.item_ref] = row.ts or ""

    latest_tr = {}
    for ev in walk["events"]:  # emitted oldest -> newest, so last write wins
        latest_tr[ev["item_id"]] = ev

    by_sid: dict[str, dict] = {}
    for iid, tr in latest_tr.items():
        last_event = {"kind": tr["kind"], "ts": tr["ts"]}
        if iid in snap.resolved_refs:
            res_ts = snap.resolutions[iid].get("ts")
            if (res_ts or "") > (last_event["ts"] or ""):
                last_event = {"kind": "resolved", "ts": res_ts}
        if latest_check.get(iid, "") > (last_event["ts"] or ""):
            last_event = {"kind": "quote_check", "ts": latest_check[iid]}
        item = walk["items"][iid]
        entry = by_sid.setdefault(tr["session_id"], {
            "counts": {"first_seen": 0, "changed": 0, "last_seen": 0}, "rows": []})
        entry["counts"][tr["kind"]] += 1
        entry["rows"].append({"id": iid, "text": item.get("text"),
                              "trust": item.get("trust"),
                              "kind": item.get("kind"),
                              "transition": tr["kind"], "last_event": last_event})

    groups = []
    for s in hist_sessions:  # newest first, only sessions that kept rows
        kept = by_sid.get(s.session_id)
        if kept is None:
            continue
        kept["rows"].sort(key=lambda r: (_TRANSITION_ORDER[r["transition"]], r["id"]))
        groups.append({"session_id": s.session_id, "created": s.created,
                       "active_topic": s.topic,
                       "counts": kept["counts"], "rows": kept["rows"]})

    return {"ok": True, "groups": groups, "head": head,
            "totals": {"objects": len(walk["items"]),
                       "events": (len(walk["events"]) + len(snap.resolved_refs)
                                  + len(ver_rows))},
            "partial": _ledger_partial(walk["unreadable"]),
            "notes": walk["notes"]}

def session_events(slug: str, sid: str) -> dict:
    """The session page: every transition the named session wrote, grouped by
    object. sid must exact-match a session_id from the session listing before
    anything is opened, same discipline as diff_checkpoints. Resolutions and
    quote checks are deliberately absent: their ledgers record no session, and
    this page must not claim them for one."""
    walk = _walk_transitions(slug)
    if sid not in {s.session_id for s in walk["sessions"]}:
        return {"ok": False, "error": {
            "what": f"Session {sid!r} isn't part of {slug}'s history.",
            "why": "The session id doesn't match any recorded checkpoint.",
            "fix": "Pick a session from the ledger's session list.",
        }}
    opened = walk["opened"].get(sid)
    if opened is None:
        return {"ok": False, "error": {
            "what": f"Session {sid} doesn't exist.",
            "why": "No checkpoint file was found for that session.",
            "fix": "Pick a session from the project's history.",
        }}
    data = opened.checkpoint
    mine = [ev for ev in walk["events"] if ev["session_id"] == sid]

    by_item: dict[str, dict] = {}
    counts = {"first_seen": 0, "changed": 0, "last_seen": 0}
    for ev in mine:
        counts[ev["kind"]] += 1
        item = walk["items"].get(ev["item_id"]) or {}
        obj = by_item.setdefault(ev["item_id"], {
            "id": ev["item_id"], "text": item.get("text"),
            "trust": item.get("trust"),
            "kind": item.get("kind"), "events": [],
            # The print view's provenance line: the stored quote's PRESENCE
            # and origin, never the quote text — this payload leaves the
            # machine for a render layer, and presence answers the question.
            "has_quote": bool(item.get("quote")),
            "origin_session": _norm_str(item.get("origin_session"))})
        obj["events"].append({"kind": ev["kind"], "ts": ev["ts"], "detail": ev["detail"]})

    objects = sorted(by_item.values(), key=lambda o: (
        _TRANSITION_ORDER[o["events"][0]["kind"]], o["id"]))

    meta, _sections, _partial = _normalize(data)
    session = {"session_id": sid, "created": meta["created"],
               "author": meta["author"], "active_topic": meta["active_topic"]}
    if _receipts_gate(slug):
        session["receipt"] = api.receipt_state(data)

    return {"ok": True, "session": session, "objects": objects, "counts": counts,
            "partial": _ledger_partial(walk["unreadable"]),
            "notes": walk["notes"]}

def _bad_item_id_error(item_id):
    return {"ok": False, "error": {
        "what": f"{item_id!r} isn't a valid item id.",
        "why": "Item ids look like a-<hex>, e.g. o-1a2b3c4d5e6f — that doesn't.",
        "fix": "Open the History diff to find current item ids.",
    }}

def _unknown_item_id_error(item_id, notes):
    why = "The id doesn't match any item across the scanned sessions."
    return {"ok": False, "error": {
        "what": f"No item with id {item_id!r} was found in this project's history.",
        "why": " ".join([why, *notes]),
        "fix": "Open the History diff to find current item ids.",
    }}

def _index_items(sections):
    """id -> normalized item + section and kind, same shape as
    diff_checkpoints' index()."""
    by_id = {}
    for sec in sections:
        for item in sec["items"]:
            iid = item.get("id")
            if iid:
                by_id[iid] = dict(item, section=sec["key"], kind=sec["kind"])
    return by_id

_BIO_TRACKED_FIELDS = ("trust", "quote_verified", "text")

def _verification_events(rows):
    out = []
    for row in rows:
        detail = (f"{row.check}: {row.reason}" if row.check and row.reason
                  else (row.reason or row.check))
        out.append({"kind": "verified", "session_id": None,
                    "ts_or_created": row.ts, "detail": detail})
    return out

def item_biography(slug: str, item_id: str) -> dict:
    """Walk a single item's life across a project's session history, oldest -> newest,
    merging in verification.jsonl and events.jsonl (resolution) rows through the view."""
    if not ITEM_ID_RE.fullmatch(item_id or ""):
        return _bad_item_id_error(item_id)

    listing, opened, snap = _open_all(slug)
    notes = list(snap.notes())

    events, chain, last_item, prev_sighting = [], [], None, None
    scanned_count = 0
    oldest_has_item = False

    for s in reversed(listing.rows):  # oldest -> newest
        op = opened.get(s.session_id)
        if op is None:
            continue  # vanished between listing and opening: skip, don't abort the walk
        _, sections, _ = _normalize(op.checkpoint)
        is_first_scanned = scanned_count == 0
        scanned_count += 1

        item = _index_items(sections).get(item_id)
        if item is None:
            continue

        created = s.created
        if prev_sighting is None:
            if is_first_scanned:
                oldest_has_item = True
            events.append({"kind": "born", "session_id": s.session_id,
                            "ts_or_created": created, "detail": item.get("quote")})
            chain.append({"session_id": s.session_id, "created": created, "changed": []})
        else:
            changed_fields = [f for f in _BIO_TRACKED_FIELDS if item.get(f) != prev_sighting.get(f)]
            if changed_fields:
                events.append({"kind": "changed", "session_id": s.session_id,
                                "ts_or_created": created, "detail": ", ".join(changed_fields)})
            else:
                events.append({"kind": "seen", "session_id": s.session_id,
                                "ts_or_created": created, "detail": None})
            chain.append({"session_id": s.session_id, "created": created,
                           "changed": changed_fields})
        prev_sighting = item
        last_item = item

    if last_item is None:
        return _unknown_item_id_error(item_id, notes)

    rows = [v for v in view.verifications(slug) if v.item_ref == item_id]
    events.extend(_verification_events(rows))

    if item_id in snap.resolved_refs:
        res = snap.resolutions[item_id]
        events.append({"kind": "resolved", "session_id": None,
                        "ts_or_created": res.get("ts"),
                        "detail": _resolution_note(res, snap)})

    events.sort(key=lambda e: e["ts_or_created"] or "")

    failures = [r for r in rows if r.reason]
    # origin_session is untrusted checkpoint content; it only ever answers "is
    # that one of this project's listed sessions", never a path.
    origin_sid = last_item.get("origin_session") or chain[0]["session_id"]
    anatomy = {
        "stored": {k: last_item.get(k) for k in
                   ("trust", "quote", "quote_verified", "last_verified", "origin_session")},
        "receipt": last_item.get("quote_provenance"),
        "chain": chain,
        "checks": {
            "origin_on_disk": origin_sid in {r.session_id for r in listing.rows},
            "quote_check_failures": len(failures),
            "last_check_ts": max((r.ts for r in failures if r.ts), default=None),
        },
    }

    window_note = "history starts here — earlier sessions may have been cleaned up" if oldest_has_item else None
    return {"ok": True, "item": last_item, "events": events,
            "window_note": window_note, "trust_anatomy": anatomy,
            "notes": notes}

# The strip renders a window, never a silent cap: what lies beyond each edge
# is named in partial. Six columns is the frozen reference's width.
GRID_COLUMNS = 6
GRID_ROWS = 30

def project_grid(slug: str) -> dict:
    """The check strip: object × checkpoint lanes over the shared walk.
    Sightings land in the column of the session that wrote them, with the
    trust class of that time. A departure marks the transition session —
    the same attribution the ledger makes — and the lane reads gone after
    it. Quote checks carry NO session attribution on disk; each is bucketed
    by ts into the earliest window column whose created stamp is >= its ts
    (later than head folds into head), and the row keeps the exact ts so
    the hover states when, not who."""
    walk = _walk_transitions(slug)
    hist = walk["sessions"]                             # newest -> oldest
    window = list(reversed(hist[:GRID_COLUMNS]))        # oldest -> newest
    older_columns = max(0, len(hist) - len(window))
    columns = [{"session_id": s.session_id, "created": s.created,
                "is_head": i == len(window) - 1}
               for i, s in enumerate(window)]
    window_ids = {c["session_id"] for c in columns}

    by_item: dict[str, dict] = {}
    latest_ts: dict[str, str] = {}
    for ev in walk["events"]:  # oldest -> newest
        iid = ev["item_id"]
        latest_ts[iid] = ev["ts"] or latest_ts.get(iid) or ""
        entry = by_item.setdefault(iid, {"cells": {}, "checks": [], "gone_after": None})
        # A re-appearance after a departure clears the dashed tail.
        entry["gone_after"] = ev["session_id"] if ev["kind"] == "last_seen" else None
        if ev["session_id"] in window_ids:
            entry["cells"][ev["session_id"]] = {
                "kind": ev["kind"], "trust": ev.get("trust"),
                "ts": ev["ts"], "detail": ev["detail"]}

    checks_before_window = 0
    # No columns means no sessions, so nothing can anchor a check row —
    # walk["items"] is empty too and the guard below would skip every row.
    ver_rows = view.verifications(slug) if columns else ()
    for row in ver_rows:
        iid = row.item_ref
        if iid is None or iid not in walk["items"]:
            continue
        ts = row.ts or ""
        if ts and ts <= (columns[0]["created"] or "") and older_columns:
            checks_before_window += 1
            continue
        column = columns[-1]["session_id"]  # later than head folds into head
        for c in columns:
            if (c["created"] or "") >= ts:
                column = c["session_id"]
                break
        entry = by_item.setdefault(iid, {"cells": {}, "checks": [], "gone_after": None})
        detail = (f"{row.check}: {row.reason}" if row.check and row.reason
                  else (row.reason or row.check))
        entry["checks"].append({"column": column, "ts": row.ts, "detail": detail})
        latest_ts[iid] = max(latest_ts.get(iid) or "", ts)

    ordered = sorted(by_item, key=lambda iid: (latest_ts.get(iid) or "", iid), reverse=True)
    shown = ordered[:GRID_ROWS]
    rows = []
    for iid in shown:
        item = walk["items"].get(iid) or {}
        entry = by_item[iid]
        rows.append({"id": iid, "text": item.get("text"), "trust": item.get("trust"),
                     "kind": item.get("kind"),
                     "cells": entry["cells"], "checks": entry["checks"],
                     "gone_after": entry["gone_after"]})

    partial = _ledger_partial(walk["unreadable"])
    if older_columns:
        partial.append(f"{older_columns} older checkpoint(s) beyond this window.")
    if len(ordered) > len(shown):
        partial.append(f"{len(ordered) - len(shown)} further object(s) beyond this window.")
    if checks_before_window:
        partial.append(f"{checks_before_window} quote check(s) predate this window.")

    return {"ok": True, "columns": columns, "rows": rows,
            "older_columns": older_columns, "partial": partial,
            "notes": walk["notes"]}
