"""Read daimon checkpoint JSON from disk. No daimon imports — files are the seam."""
import base64
import hashlib
import json
import re
from pathlib import Path

POINTER_RE = re.compile(r"^(latest|prev-[1-9][0-9]?)$")
ITEM_ID_RE = re.compile(r"^[a-z]-[0-9a-f]{6,40}(-\d+)?$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")

# vitni/0.2 outputs_hash: multibase base64url-nopad ("u") over multihash
# sha2-256 (multicodec 0x12 + 32-byte length 0x20). Re-derived here rather
# than imported — reader.py has no daimon imports, files are the seam.
_MULTIHASH_SHA256 = bytes([0x12, 0x20])

def _multihash_b64(raw: bytes) -> str:
    digest = _MULTIHASH_SHA256 + hashlib.sha256(raw).digest()
    return "u" + base64.urlsafe_b64encode(digest).decode().rstrip("=")

def receipt_state(data_dir: Path, data: dict) -> dict:
    """Cheap tamper check only: sidecar present and outputs_hash covers the ROOT
    session file's bytes (<data_dir>/<session_id>.json) — a receipt is a statement
    about a SESSION, keyed by session_id, not about whichever pointer snapshot the
    caller happened to open. daimon writes several pointer copies during one
    session but only binds the receipt to the final root bytes, so this is resolved
    from session_id regardless of what path the checkpoint view is showing.
    NOT signature verification — that is `daimon verify-receipt` and needs the vitni
    CLI. Absence of a claim is quiet; a broken claim is loud. Never raises: a receipt
    problem must not break the checkpoint view.
    """
    if not isinstance(data, dict):
        return {"state": "unsigned", "detail": None}
    if data.get("receipts") is not True:
        return {"state": "unsigned", "detail": None}

    sid = data.get("session_id")
    # sid arrives from file content and is about to be joined to a path — twice.
    if not isinstance(sid, str) or not SESSION_ID_RE.fullmatch(sid):
        return {"state": "missing", "detail": None}

    sidecar = data_dir / f"{sid}.receipt"
    try:
        want = json.loads(sidecar.read_text(encoding="utf-8"))["receipt"]["outputs_hash"]
        if not isinstance(want, str):
            raise KeyError("outputs_hash")
    except FileNotFoundError:
        return {"state": "missing", "detail": None}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"state": "missing", "detail": f"{sidecar.name} is not a readable receipt"}
    except (KeyError, TypeError):
        # Sidecar parsed fine as JSON — it just doesn't carry outputs_hash. That is
        # not an unreadable file, so it gets no detail claiming otherwise.
        return {"state": "missing", "detail": None}

    root = data_dir / f"{sid}.json"
    try:
        got = _multihash_b64(root.read_bytes())
    except OSError:
        return {"state": "missing", "detail": None}

    return {"state": "match" if got == want else "mismatch", "detail": None}

def receipts_enabled(bucket: Path) -> bool:
    """Has this project opted into receipts? Answered from the pointer files the
    sidebar already reads, so a project that never enabled them shows nothing at
    all rather than being nagged about a feature it declined."""
    for _ref, path in _pointer_files(bucket):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed.get("receipts") is True:
            return True
    return False

def resolve_data_dir(env=None):
    import os
    env = os.environ if env is None else env
    raw = env.get("DAIMON_CHECKPOINT_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "checkpoints"

def project_slug(project_dir):
    return re.sub(r"[^\w-]", "-", str(project_dir).strip())

def _pointer_files(bucket: Path):
    if not bucket.is_dir():
        return []
    out = []
    for p in bucket.iterdir():
        ref = p.name.removesuffix(".json")
        if p.suffix == ".json" and p.name.endswith(".json") and POINTER_RE.fullmatch(ref):
            out.append((ref, p))
    def order(item):
        ref, _ = item
        return 0 if ref == "latest" else int(ref.split("-")[1])
    return sorted(out, key=order)

def list_buckets(data_dir: Path) -> list[dict]:
    """Discover project buckets under data_dir. A bucket is any subdir containing latest.json
    (mirrors daimon's own store.list_buckets() semantics) — this naturally skips sidecar dirs
    like .chunk-cache/ and .partials/ without needing to name them."""
    if not data_dir.is_dir():
        return []
    out = []
    for p in data_dir.iterdir():
        if not p.is_dir():
            continue
        latest = p / "latest.json"
        if not latest.is_file():
            continue
        created = active_topic = item_count = project_name = None
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            created = data.get("created")
            # #672 write-time stamp; None when the bucket predates it.
            raw_name = data.get("project_name")
            project_name = raw_name if isinstance(raw_name, str) and raw_name else None
            wc = data.get("working_context") or {}
            es = data.get("epistemic_snapshot") or {}
            topic = wc.get("active_topic") if isinstance(wc, dict) else None
            active_topic = topic.get("text") if isinstance(topic, dict) else None
            count = 0
            for container, key in (
                (wc, "open_questions"), (wc, "recent_decisions"),
                (es, "strong_beliefs"), (es, "uncertainties"), (es, "contradictions_flagged"),
            ):
                v = container.get(key) if isinstance(container, dict) else None
                if isinstance(v, list):
                    count += len(v)
            item_count = count
        except (OSError, json.JSONDecodeError, AttributeError):
            pass  # torn latest.json: keep the bucket listed, fields stay None
        out.append({"slug": p.name, "project_name": project_name, "created": created,
                    "active_topic": active_topic, "item_count": item_count})
    out.sort(key=lambda b: b["created"] or "", reverse=True)  # "" sorts lowest, so None lands last
    return out

def list_recent(bucket: Path):
    out = []
    for ref, path in _pointer_files(bucket):
        created = topic = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            created = data.get("created")
            topic = (data.get("working_context", {}).get("active_topic") or {}).get("text")
        except (OSError, json.JSONDecodeError, AttributeError):
            pass  # torn pointer: keep the entry, fields stay None
        out.append({"ref": ref, "created": created, "active_topic": topic})
    return out

KNOWN_FORMAT = "D-018"

_SECTIONS = [  # (ui key, label, checkpoint container, checkpoint key)
    ("decisions", "Decisions", "working_context", "recent_decisions"),
    # container/key unused: open_loops is derived from open_questions minus external_state items (see loop below)
    ("open_loops", "Open loops", "working_context", "open_questions"),
    ("beliefs", "Beliefs", "epistemic_snapshot", "strong_beliefs"),
    ("uncertainties", "Uncertainties", "epistemic_snapshot", "uncertainties"),
    ("contradictions", "Contradictions", "epistemic_snapshot", "contradictions_flagged"),
]

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
    return {
        "verifier": verifier_id,
        "verifier_version": verifier_version,
        "outcome": _norm_str(raw.get("outcome")),
        "checked_at": _norm_str(raw.get("checked_at")),
        "digest_algorithm": _norm_str(digest.get("algorithm")),
        "message_ids": _norm_str_list(binding.get("message_ids")),
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

def _normalize(data):
    """Turn raw checkpoint JSON into (meta, sections, partial). Shared by load_checkpoint
    (pointer-based) and diff_checkpoints (arbitrary session files) — the seam that lets
    diff reuse checkpoint normalization without going through the pointer chain."""
    partial = []
    fv = data.get("format_version")
    if fv != KNOWN_FORMAT:
        partial.append(f"Checkpoint uses schema {fv or 'unknown'}; this inspector understands {KNOWN_FORMAT}. Showing what's readable.")

    wc = data.get("working_context") or {}
    es = data.get("epistemic_snapshot") or {}
    containers = {"working_context": wc, "epistemic_snapshot": es}
    topic = (wc.get("active_topic") or {}) if isinstance(wc.get("active_topic"), dict) else {}

    def items_for(container, key):
        raw = containers[container].get(key)
        if raw is None:
            return []
        if not isinstance(raw, list):
            partial.append(f"Section '{key}' has an unexpected shape and was skipped.")
            return []
        return [i for i in (_norm_item(r) for r in raw) if i is not None]

    all_questions = items_for("working_context", "open_questions")
    sections = [
        {"key": "verify_first", "label": "Verify before trusting",
         "items": [i for i in all_questions if i["external_state"]]},
    ]
    for ui_key, label, container, cp_key in _SECTIONS:
        if ui_key == "open_loops":
            items = [i for i in all_questions if not i["external_state"]]
        else:
            items = items_for(container, cp_key)
        sections.append({"key": ui_key, "label": label, "items": items})

    meta = {
        "created": data.get("created"),
        "author": data.get("author"),
        "format_version": fv,
        "session_id": data.get("session_id"),
        "active_topic": topic.get("text"),
    }
    return meta, sections, partial

def load_checkpoint(data_dir: Path, slug: str, ref: str):
    bucket = data_dir / slug
    if not POINTER_RE.fullmatch(ref or ""):
        return {"ok": False, "error": {
            "what": f"Checkpoint reference {ref!r} isn't one this inspector serves.",
            "why": "Only 'latest' and 'prev-N' pointers are served.",
            "fix": "Pick a checkpoint from the sidebar.",
        }}
    path = bucket / f"{ref}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "error": {
            "what": f"Checkpoint {ref} doesn't exist.",
            "why": "The pointer chain is shorter than requested.",
            "fix": "Pick a checkpoint from the sidebar.",
        }}
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": {
            "what": f"Couldn't read checkpoint {ref}.",
            "why": "The file isn't complete JSON — possibly a partial write.",
            "fix": "Re-run `daimon heal`, or pick another checkpoint from the sidebar.",
        }}

    meta, sections, partial = _normalize(data)
    if receipts_enabled(bucket):
        meta["receipt"] = receipt_state(data_dir, data)
    return {"ok": True, "partial": partial, "sections": sections, "meta": meta}

def project_history(data_dir: Path, slug: str):
    sessions, unreadable = [], 0
    if data_dir.is_dir():
        for p in data_dir.iterdir():
            if not p.is_file() or p.suffix != ".json":
                continue
            if POINTER_RE.fullmatch(p.name.removesuffix(".json")):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                unreadable += 1
                continue
            if not isinstance(data, dict) or data.get("project_slug") != slug:
                continue
            wc = data.get("working_context") or {}
            topic = wc.get("active_topic") if isinstance(wc.get("active_topic"), dict) else {}
            sessions.append({
                "session_id": p.name.removesuffix(".json"),
                "created": data.get("created"),
                "active_topic": (topic or {}).get("text"),
            })
    sessions.sort(key=lambda s: s["created"] or "", reverse=True)
    return {"sessions": sessions, "unreadable": unreadable}

def resolutions(bucket: Path) -> dict:
    """Fold events.jsonl into last-event-per-item_ref, keeping only resolved ones.
    Missing bucket/events.jsonl, or a file with no readable resolution events, is {} —
    not an error. Malformed lines are skipped silently (append-log, may be torn mid-write)."""
    try:
        text = (bucket / "events.jsonl").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    folded = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("kind") != "resolution":
            continue
        ref = ev.get("item_ref")
        if not ref:
            continue
        folded[ref] = ev
    return {
        ref: {"ts": ev.get("ts"), "note": ev.get("note")}
        for ref, ev in folded.items()
        if ev.get("status") == "resolved"
    }

def _activity_events(bucket: Path):
    rows = []
    for ev in _jsonl_rows(bucket / "events.jsonl"):
        kind = _norm_str(ev.get("kind")) or "unknown"
        note = _norm_str(ev.get("note"))
        item_text = _norm_str(ev.get("item_text"))
        status = _norm_str(ev.get("status"))
        if kind == "corroboration":
            detail = item_text or note or status
        elif kind == "handoff":
            detail = note
        else:
            detail = note or item_text
        rows.append({
            "ts": _norm_str(ev.get("ts")), "kind": kind, "session_id": None,
            "item_ref": _norm_str(ev.get("item_ref")), "detail": detail,
            "extra": {"status": status, "source": _norm_str(ev.get("source")),
                       "item_text": item_text},
        })
    return rows

def _activity_quote_checks(bucket: Path):
    rows = []
    for ev in _jsonl_rows(bucket / "verification.jsonl"):
        check = _norm_str(ev.get("check"))
        reason = _norm_str(ev.get("reason"))
        detail = f"{check}: {reason}" if check and reason else (reason or check)
        rows.append({
            "ts": _norm_str(ev.get("ts")), "kind": "quote_check", "session_id": None,
            "item_ref": _norm_str(ev.get("item_ref")), "detail": detail,
            "extra": {"check": check, "reason": reason},
        })
    return rows

def project_activity(data_dir: Path, slug: str) -> dict:
    """One chronological feed per project: session markers + events.jsonl +
    verification.jsonl, newest -> oldest. Every row is literally on disk."""
    hist = project_history(data_dir, slug)
    rows = []
    for s in hist["sessions"]:
        topic = _norm_str(s.get("active_topic"))
        rows.append({"ts": _norm_str(s.get("created")), "kind": "session",
                      "session_id": s["session_id"], "item_ref": None,
                      "detail": topic, "extra": {"topic": topic}})
    bucket = data_dir / slug
    rows.extend(_activity_events(bucket))
    rows.extend(_activity_quote_checks(bucket))
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    partial = []
    unreadable = hist.get("unreadable") or 0
    if unreadable:
        partial.append(f"{unreadable} session file(s) couldn't be read and are missing from the feed.")
    return {"ok": True, "rows": rows, "partial": partial}

def _load_session(data_dir: Path, sid: str):
    path = data_dir / f"{sid}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, {
            "what": f"Session {sid} doesn't exist.",
            "why": "No checkpoint file was found for that session.",
            "fix": "Pick a session from the project's history.",
        }
    except (OSError, json.JSONDecodeError):
        return None, {
            "what": f"Couldn't read session {sid}.",
            "why": "The file isn't complete JSON — possibly a partial write.",
            "fix": "Re-run `daimon heal`, or pick another session from the history.",
        }
    return data, None

def diff_checkpoints(data_dir: Path, slug: str, sid_a: str, sid_b: str) -> dict:
    """A=older, B=newer. sid_a/sid_b must exact-match a session_id from project_history —
    checked before any path is built, so raw input never reaches the filesystem."""
    valid_ids = {s["session_id"] for s in project_history(data_dir, slug)["sessions"]}
    for sid in (sid_a, sid_b):
        if sid not in valid_ids:
            return {"ok": False, "error": {
                "what": f"Session {sid!r} isn't part of {slug}'s history.",
                "why": "The session id doesn't match any recorded checkpoint.",
                "fix": "Pick a session from the project's history list.",
            }}

    data_a, err = _load_session(data_dir, sid_a)
    if err:
        return {"ok": False, "error": err}
    data_b, err = _load_session(data_dir, sid_b)
    if err:
        return {"ok": False, "error": err}

    meta_a, sections_a, partial_a = _normalize(data_a)
    meta_b, sections_b, partial_b = _normalize(data_b)

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

    res_fold = resolutions(data_dir / slug)

    born = [map_b[i] for i in sorted(ids_b - ids_a)]
    resolved, gone = [], []
    for iid in sorted(ids_a - ids_b):
        item = map_a[iid]
        ev = res_fold.get(iid)
        if ev:
            resolved.append({"item": item, "note": ev.get("note"), "ts": ev.get("ts")})
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
        "partial": partial,
    }

def _walk_transitions(data_dir: Path, slug: str):
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
    hist = project_history(data_dir, slug)
    events, latest_item, last_sight = [], {}, {}
    prev_ids, gone = None, set()
    for s in reversed(hist["sessions"]):  # oldest -> newest
        data, err = _load_session(data_dir, s["session_id"])
        if err:
            continue  # torn/missing session file: skip, don't abort the whole walk
        _, sections, _ = _normalize(data)
        cur = _index_items(sections)
        for iid, item in cur.items():
            if iid not in latest_item or iid in gone:
                gone.discard(iid)
                events.append({"item_id": iid, "kind": "first_seen", "ts": s["created"],
                               "session_id": s["session_id"], "detail": None,
                               "trust": item.get("trust")})
            else:
                changed = [f for f in _BIO_TRACKED_FIELDS
                           if item.get(f) != latest_item[iid].get(f)]
                if changed:
                    events.append({"item_id": iid, "kind": "changed", "ts": s["created"],
                                   "session_id": s["session_id"],
                                   "detail": ", ".join(changed),
                                   "trust": item.get("trust")})
            latest_item[iid] = item
        if prev_ids is not None:
            for iid in sorted(prev_ids - set(cur)):
                gone.add(iid)
                events.append({"item_id": iid, "kind": "last_seen",
                               "ts": last_sight.get(iid),
                               "session_id": s["session_id"], "detail": None,
                               "trust": latest_item[iid].get("trust")})
        for iid in cur:
            last_sight[iid] = s["created"]
        prev_ids = set(cur)
    return {"events": events, "items": latest_item, "unreadable": hist["unreadable"]}

# Row order inside a ledger group and a session page: births, then changes,
# then departures — the order the header counts read in — id-tiebroken so the
# output never depends on dict insertion.
_TRANSITION_ORDER = {"first_seen": 0, "changed": 1, "last_seen": 2}

# Viewer section key -> the kind word recall already prints (schema.ITEM_FIELDS
# vocabulary: question/decision/belief/uncertainty/contradiction). The ledger's
# chip must say exactly what a recall row for the same item says — two surfaces,
# one vocabulary. verify_first and open_loops are both slices of open_questions.
_SECTION_KIND = {"verify_first": "question", "open_loops": "question",
                 "decisions": "decision", "beliefs": "belief",
                 "uncertainties": "uncertainty", "contradictions": "contradiction"}

def _ledger_partial(unreadable):
    if not unreadable:
        return []
    return [f"{unreadable} session file(s) couldn't be read and are missing from the ledger."]

def project_ledger(data_dir: Path, slug: str) -> dict:
    """The ledger screen: one row per object, grouped under the session of its
    latest recorded transition. A later resolution or quote check (own ts, no
    session attribution in their ledgers) can overtake the LAST EVENT field,
    but never moves the row to another group — the viewer must not invent the
    attribution the stored rows don't carry."""
    walk = _walk_transitions(data_dir, slug)
    hist_sessions = project_history(data_dir, slug)["sessions"]  # newest -> oldest
    head = None
    if hist_sessions:
        head = {"session_id": hist_sessions[0]["session_id"],
                "created": hist_sessions[0]["created"]}

    bucket = data_dir / slug
    res_fold = resolutions(bucket)
    ver_rows = _jsonl_rows(bucket / "verification.jsonl")

    latest_tr = {}
    for ev in walk["events"]:  # emitted oldest -> newest, so last write wins
        latest_tr[ev["item_id"]] = ev

    by_sid = {}
    for iid, tr in latest_tr.items():
        last_event = {"kind": tr["kind"], "ts": tr["ts"]}
        res = res_fold.get(iid)
        if res and (res.get("ts") or "") > (last_event["ts"] or ""):
            last_event = {"kind": "resolved", "ts": res.get("ts")}
        for row in ver_rows:
            if row.get("item_ref") == iid and (row.get("ts") or "") > (last_event["ts"] or ""):
                last_event = {"kind": "quote_check", "ts": row.get("ts")}
        item = walk["items"][iid]
        entry = by_sid.setdefault(tr["session_id"], {
            "counts": {"first_seen": 0, "changed": 0, "last_seen": 0}, "rows": []})
        entry["counts"][tr["kind"]] += 1
        entry["rows"].append({"id": iid, "text": item.get("text"),
                              "trust": item.get("trust"),
                              "kind": _SECTION_KIND.get(item.get("section")),
                              "transition": tr["kind"], "last_event": last_event})

    groups = []
    for s in hist_sessions:  # newest first, only sessions that kept rows
        entry = by_sid.get(s["session_id"])
        if entry is None:
            continue
        entry["rows"].sort(key=lambda r: (_TRANSITION_ORDER[r["transition"]], r["id"]))
        groups.append({"session_id": s["session_id"], "created": s["created"],
                       "active_topic": s.get("active_topic"),
                       "counts": entry["counts"], "rows": entry["rows"]})

    return {"ok": True, "groups": groups, "head": head,
            "totals": {"objects": len(walk["items"]),
                       "events": len(walk["events"]) + len(res_fold) + len(ver_rows)},
            "partial": _ledger_partial(walk["unreadable"])}

def session_events(data_dir: Path, slug: str, sid: str) -> dict:
    """The session page: every transition the named session wrote, grouped by
    object. sid must exact-match a session_id from project_history before any
    path is built, same discipline as diff_checkpoints. Resolutions and quote
    checks are deliberately absent: their ledgers record no session, and this
    page must not claim them for one."""
    valid_ids = {s["session_id"] for s in project_history(data_dir, slug)["sessions"]}
    if sid not in valid_ids:
        return {"ok": False, "error": {
            "what": f"Session {sid!r} isn't part of {slug}'s history.",
            "why": "The session id doesn't match any recorded checkpoint.",
            "fix": "Pick a session from the ledger's session list.",
        }}
    data, err = _load_session(data_dir, sid)
    if err:
        return {"ok": False, "error": err}

    walk = _walk_transitions(data_dir, slug)
    mine = [ev for ev in walk["events"] if ev["session_id"] == sid]

    by_item = {}
    counts = {"first_seen": 0, "changed": 0, "last_seen": 0}
    for ev in mine:
        counts[ev["kind"]] += 1
        item = walk["items"].get(ev["item_id"]) or {}
        obj = by_item.setdefault(ev["item_id"], {
            "id": ev["item_id"], "text": item.get("text"),
            "trust": item.get("trust"),
            "kind": _SECTION_KIND.get(item.get("section")), "events": [],
            # The print view's provenance line: the stored quote's PRESENCE
            # and origin, never the quote text — this payload leaves the
            # machine for a render layer, and presence answers the question.
            "has_quote": bool(item.get("quote")),
            "origin_session": _norm_str(item.get("origin_session"))})
        obj["events"].append({"kind": ev["kind"], "ts": ev["ts"], "detail": ev["detail"]})

    objects = sorted(by_item.values(), key=lambda o: (
        _TRANSITION_ORDER[o["events"][0]["kind"]], o["id"]))

    wc = data.get("working_context") or {}
    topic = wc.get("active_topic") if isinstance(wc.get("active_topic"), dict) else {}
    session = {"session_id": sid, "created": data.get("created"),
               "author": data.get("author"),
               "active_topic": (topic or {}).get("text")}
    if receipts_enabled(data_dir / slug):
        session["receipt"] = receipt_state(data_dir, data)

    return {"ok": True, "session": session, "objects": objects, "counts": counts,
            "partial": _ledger_partial(walk["unreadable"])}

def _bad_item_id_error(item_id):
    return {"ok": False, "error": {
        "what": f"{item_id!r} isn't a valid item id.",
        "why": "Item ids look like a-<hex>, e.g. o-1a2b3c4d5e6f — that doesn't.",
        "fix": "Open the History diff to find current item ids.",
    }}

def _unknown_item_id_error(item_id):
    return {"ok": False, "error": {
        "what": f"No item with id {item_id!r} was found in this project's history.",
        "why": "The id doesn't match any item across the scanned sessions.",
        "fix": "Open the History diff to find current item ids.",
    }}

def _index_items(sections):
    """id -> normalized item + section, same shape as diff_checkpoints' index()."""
    by_id = {}
    for sec in sections:
        for item in sec["items"]:
            iid = item.get("id")
            if iid:
                by_id[iid] = dict(item, section=sec["key"])
    return by_id

_BIO_TRACKED_FIELDS = ("trust", "quote_verified", "text")

def _jsonl_rows(path: Path):
    """Defensive line-by-line jsonl parse: torn/non-dict lines skipped,
    missing/unreadable file -> []. Shared by verification + activity readers."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            rows.append(ev)
    return rows

def _verification_rows(bucket: Path, item_id: str):
    return [ev for ev in _jsonl_rows(bucket / "verification.jsonl")
            if ev.get("item_ref") == item_id]

def _verification_events(bucket: Path, item_id: str):
    out = []
    for ev in _verification_rows(bucket, item_id):
        check, reason = ev.get("check"), ev.get("reason")
        detail = f"{check}: {reason}" if check and reason else (reason or check)
        out.append({"kind": "verified", "session_id": None,
                    "ts_or_created": ev.get("ts"), "detail": detail})
    return out

def item_biography(data_dir: Path, slug: str, item_id: str) -> dict:
    """Walk a single item's life across a project's session history, oldest -> newest,
    merging in verification.jsonl and events.jsonl (resolution) rows from the bucket."""
    if not ITEM_ID_RE.fullmatch(item_id or ""):
        return _bad_item_id_error(item_id)

    sessions = list(reversed(project_history(data_dir, slug)["sessions"]))  # oldest -> newest

    events, chain, last_item, prev_sighting = [], [], None, None
    scanned_count = 0
    oldest_has_item = False

    for s in sessions:
        data, err = _load_session(data_dir, s["session_id"])
        if err:
            continue  # torn/missing session file: skip, don't abort the whole walk
        _, sections, _ = _normalize(data)
        is_first_scanned = scanned_count == 0
        scanned_count += 1

        item = _index_items(sections).get(item_id)
        if item is None:
            continue

        created = s["created"]
        if prev_sighting is None:
            if is_first_scanned:
                oldest_has_item = True
            events.append({"kind": "born", "session_id": s["session_id"],
                            "ts_or_created": created, "detail": item.get("quote")})
            chain.append({"session_id": s["session_id"], "created": created, "changed": []})
        else:
            changed_fields = [f for f in _BIO_TRACKED_FIELDS if item.get(f) != prev_sighting.get(f)]
            if changed_fields:
                events.append({"kind": "changed", "session_id": s["session_id"],
                                "ts_or_created": created, "detail": ", ".join(changed_fields)})
            else:
                events.append({"kind": "seen", "session_id": s["session_id"],
                                "ts_or_created": created, "detail": None})
            chain.append({"session_id": s["session_id"], "created": created,
                           "changed": changed_fields})
        prev_sighting = item
        last_item = item

    if last_item is None:
        return _unknown_item_id_error(item_id)

    bucket = data_dir / slug
    events.extend(_verification_events(bucket, item_id))

    res = resolutions(bucket).get(item_id)
    if res:
        events.append({"kind": "resolved", "session_id": None,
                        "ts_or_created": res.get("ts"), "detail": res.get("note")})

    events.sort(key=lambda e: e["ts_or_created"] or "")

    rows = _verification_rows(bucket, item_id)
    failures = [r for r in rows if r.get("reason")]
    # origin_session is untrusted checkpoint content -- a value like "../../x" would turn
    # origin_on_disk into an existence oracle over arbitrary .json paths, so it must match
    # a conservative session-id shape before it's used in path construction. chain[0]'s
    # session_id comes from actual scanned filenames and is already safe, but it's run
    # through the same guard for uniformity.
    origin_sid = last_item.get("origin_session") or chain[0]["session_id"]
    origin_sid_safe = origin_sid if SESSION_ID_RE.fullmatch(origin_sid or "") else None
    anatomy = {
        "stored": {k: last_item.get(k) for k in
                   ("trust", "quote", "quote_verified", "last_verified", "origin_session")},
        "receipt": last_item.get("quote_provenance"),
        "chain": chain,
        "checks": {
            "origin_on_disk": bool(
                origin_sid_safe and (data_dir / f"{origin_sid_safe}.json").exists()),
            "quote_check_failures": len(failures),
            "last_check_ts": max((r.get("ts") for r in failures if r.get("ts")), default=None),
        },
    }

    window_note = "history starts here — earlier sessions may have been cleaned up" if oldest_has_item else None
    return {"ok": True, "item": last_item, "events": events,
            "window_note": window_note, "trust_anatomy": anatomy}

# The strip renders a window, never a silent cap: what lies beyond each edge
# is named in partial. Six columns is the frozen reference's width.
GRID_COLUMNS = 6
GRID_ROWS = 30

def project_grid(data_dir: Path, slug: str) -> dict:
    """The check strip: object × checkpoint lanes over the shared walk.
    Sightings land in the column of the session that wrote them, with the
    trust class of that time. A departure marks the transition session —
    the same attribution the ledger makes — and the lane reads gone after
    it. Quote checks carry NO session attribution on disk; each is bucketed
    by ts into the earliest window column whose created stamp is >= its ts
    (later than head folds into head), and the row keeps the exact ts so
    the hover states when, not who."""
    walk = _walk_transitions(data_dir, slug)
    hist = project_history(data_dir, slug)["sessions"]  # newest -> oldest
    window = list(reversed(hist[:GRID_COLUMNS]))        # oldest -> newest
    older_columns = max(0, len(hist) - len(window))
    columns = [{"session_id": s["session_id"], "created": s["created"],
                "is_head": i == len(window) - 1}
               for i, s in enumerate(window)]
    window_ids = {c["session_id"] for c in columns}

    by_item, latest_ts = {}, {}
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
    ver_rows = _jsonl_rows(data_dir / slug / "verification.jsonl") if columns else []
    for row in ver_rows:
        iid = row.get("item_ref")
        if iid not in walk["items"]:
            continue
        ts = _norm_str(row.get("ts")) or ""
        if ts and ts <= (columns[0]["created"] or "") and older_columns:
            checks_before_window += 1
            continue
        column = columns[-1]["session_id"]  # later than head folds into head
        for c in columns:
            if (c["created"] or "") >= ts:
                column = c["session_id"]
                break
        entry = by_item.setdefault(iid, {"cells": {}, "checks": [], "gone_after": None})
        check, reason = _norm_str(row.get("check")), _norm_str(row.get("reason"))
        detail = f"{check}: {reason}" if check and reason else (reason or check)
        entry["checks"].append({"column": column, "ts": row.get("ts"), "detail": detail})
        latest_ts[iid] = max(latest_ts.get(iid) or "", ts)

    ordered = sorted(by_item, key=lambda iid: (latest_ts.get(iid) or "", iid), reverse=True)
    shown = ordered[:GRID_ROWS]
    rows = []
    for iid in shown:
        item = walk["items"].get(iid) or {}
        entry = by_item[iid]
        rows.append({"id": iid, "text": item.get("text"), "trust": item.get("trust"),
                     "kind": _SECTION_KIND.get(item.get("section")),
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
            "older_columns": older_columns, "partial": partial}
