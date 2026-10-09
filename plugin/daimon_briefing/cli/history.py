"""Read the checkpoint chain that is already on disk — `diff`, `blame` (#975).

A project bucket retains the last `DAIMON_CHECKPOINT_HISTORY` writes as
`latest.json` plus `prev-1.json` .. `prev-(history-1).json`, and item identity
chains across them since #678. The lineage was therefore already written and
had no reader: answering "what changed between these two sessions" meant
opening JSON by hand.

Both verbs in this module are PURE READERS. They stamp nothing, append no
event, and never write a checkpoint.

Scope is one project's own bucket, always. The flat `latest.json` at the store
root is the GLOBAL pointer and may hold any project's session, so it is never
read here: a chain that cannot be attributed to this project is no chain at
all (scar 0055 — rendering is a write, and copying another bucket's record
text out of its owner is exactly what a read verb must not do).

Shared helpers that live in the package `__init__` are reached through the
module object (`_cli.<name>`), the same seam every other verb family uses.
"""

import json
import sys

import daimon_briefing.cli as _cli

from .. import config, display, inspector, render, schema, store, view


SCHEMA_VERSION = 1


# Emission order for a diff. Additions first because they are what a reader
# scans for; the gone classes last, grouped, because their REASON is the part
# that carries weight and reads better together; `withheld` closes the list
# because it has no reason to give. There is no `forgotten` class: a listing
# never names a forgotten value (R2), so after a real forget the item is in
# neither body and has no row.
_CHANGE_ORDER = ("added", "restated", "retagged", "resolved", "superseded",
                 "dropped", "withheld")

_MARKERS = {"added": "+", "restated": "~", "retagged": "~", "withheld": "?"}

# What a withheld row's `where` can say: the item is withheld in the older
# generation, the newer one or both.
_WHERE = {(True, False): "from", (False, True): "to", (True, True): "both"}


def _pointer_entry(pointer: view.Pointer) -> dict:
    """One pointer of the window as a diff endpoint. `checkpoint` is the
    `view.Opened` (the body with every withheld item removed) of a readable
    pointer and None of a torn one, which stays listed: a diff that quietly
    jumped a generation would describe a change span it did not read."""
    index = 0 if pointer.ref == "latest" else int(pointer.ref.split("-")[1])
    meta = pointer.meta
    return {
        "index": index,
        "pointer": f"{pointer.ref}.json",
        "checkpoint": pointer.opened if pointer.readable else None,
        "session_id": meta.session_id if meta else None,
        "created": meta.created if meta else None,
    }


def _endpoint(entry: dict | None) -> dict | None:
    """The JSON shape for one end of a diff. Key order is the contract."""
    if entry is None:
        return None
    return {
        "index": entry["index"],
        "pointer": entry["pointer"],
        "session_id": entry["session_id"],
        "created": entry["created"],
    }


def _items_by_id(checkpoint: dict) -> dict:
    """`{item_id: (kind, item)}` for one checkpoint body.

    Keyed on the id `policy.stamp_item_ids` wrote, never on text: text is what
    a restatement changes, and matching on it would report one item twice (the
    false-merge lesson, #13). Non-dict entries are skipped —
    contradictions_flagged may hold bare strings, which carry no id."""
    out: dict = {}
    for field in schema.ITEM_FIELDS:
        if field.singleton:
            continue
        for item in ((checkpoint.get(field.section) or {}).get(
                field.key) or []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id and item_id not in out:
                out[item_id] = (field.kind, item)
    return out


def _gone_reason(lifecycle: str, event) -> tuple[str, str]:
    """Why an item present in the older checkpoint is absent from the newer
    one, read off the project's own lifecycle ledger. `lifecycle` is the
    ledger's word for the id (`view.lifecycle_word`) and `event` its judged
    latest event (`view.latest_event`).

    Three answers, and the third is the honest one. `dropped` means the record
    does not say: carry expired the item by weight, or the session simply did
    not restate it. Naming that as a resolution would invent a decision nobody
    made. A forget tombstone is never a reason here: a forgotten item is in
    neither body, and an event that names a tombstone reads as an unexplained
    drop rather than as anything about the value."""
    if event is None or event.tombstone:
        return "dropped", "no lifecycle event recorded"
    status = event.status or ""
    source = event.source or "unknown"
    if lifecycle == "superseded":
        target = status.split(":", 1)[1].strip() if ":" in status else ""
        return "superseded", (
            f"superseded by {target or 'an unnamed item'} (via {source})")
    if lifecycle == "resolved":
        return "resolved", f"resolved via {source}"
    return "dropped", f"latest event ({status}) does not close it"


def _withheld_in(opened) -> dict:
    """`{item_id: Withheld}` for the items of one generation the reader may
    not see and a listing may say are there: a quarantine or an unreadable
    trust ledger. A forgotten item is not among them (it is treated as absent
    on both sides, so no listing can name, count or list it)."""
    return {w.item_id: w for w in opened.withheld
            if w.item_id and w.reason in ("quarantine", "closed")}


def _was(old_text, snap) -> str:
    """The `was "<old wording>"` detail of a restated row, or a mask when that
    whole wording is a value the reader may not see. A forgotten wording is
    masked without a reason: naming it would confirm that it was forgotten."""
    verdict = view.prose_verdict(old_text, snap, closed_masks=True)
    if verdict is None:
        return f"was \"{old_text}\""
    if verdict.reason == "forgotten":
        return "was (previous wording not shown)"
    return f"was {display.withheld_marker(verdict)}"


def changes(older, newer, snap) -> list[dict]:
    """Every change between two generations (`view.Opened`), one row per id.

    Seven classes. `added` and the gone classes come from set difference on
    the ids a reader may see. `retagged` is a trust change under a stable id,
    the #974 stitched quote demotion and a `reverify` both land here.
    `restated` is a TEXT change under a stable id, which only carry's twin
    path can produce (#980): a native item inherits the previous item's
    identity while saying it in new words. An item can be both retagged and
    restated. Restated wins the single row: the text is what a reader
    compares first, and the row carries both trust values anyway.

    `withheld` closes the listing for an id the reader may not see on at least
    one side (a quarantine, or an unreadable trust ledger): `where` says which
    generation it is withheld in, and the text is printed only for the side
    where the item is visible. A released quarantine is therefore never a
    false `added`, and a new one never a false `gone`. A forgotten item has
    no row at all."""
    before = _items_by_id(older.checkpoint or {})
    after = _items_by_id(newer.checkpoint or {})
    held_before, held_after = _withheld_in(older), _withheld_in(newer)
    rows: list[dict] = []
    for item_id in {*before, *after, *held_before, *held_after}:
        in_before, in_after = item_id in before, item_id in after
        was_held = item_id in held_before and not in_before
        now_held = item_id in held_after and not in_after
        if was_held or now_held:
            verdict = (held_after if now_held else held_before)[item_id]
            kind, item = (after if in_after else before).get(
                item_id, (verdict.kind, {}))
            rows.append(_row("withheld", item_id, kind, item, None,
                             display.withheld_marker(verdict),
                             _WHERE[(was_held, now_held)]))
        elif in_before and in_after:
            kind, item = after[item_id]
            _prev_kind, prev = before[item_id]
            prev_trust, trust = prev.get("trust"), item.get("trust")
            if prev.get("text") != item.get("text"):
                rows.append(_row("restated", item_id, kind, item, prev_trust,
                                 _was(prev.get("text"), snap)))
            elif prev_trust != trust:
                rows.append(_row("retagged", item_id, kind, item, prev_trust,
                                 f"was {prev_trust or 'untagged'}"))
        elif in_after:
            kind, item = after[item_id]
            rows.append(_row("added", item_id, kind, item, None, None))
        elif in_before:
            kind, item = before[item_id]
            reason, detail = _gone_reason(
                view.lifecycle_word(snap.resolutions.get(item_id)),
                view.latest_event(snap, item_id))
            rows.append(_row(reason, item_id, kind, item, None, detail))
    rows.sort(key=lambda r: (_CHANGE_ORDER.index(r["change"]), r["item_id"]))
    return rows


def _row(change: str, item_id: str, kind: str, item: dict,
         previous_trust, detail, where=None) -> dict:
    """One change row. Key order is the documented `--json` contract; every
    key is always present so a consumer never branches on shape."""
    return {
        "change": change,
        "item_id": item_id,
        "kind": kind,
        "trust": item.get("trust"),
        "previous_trust": previous_trust,
        "text": item.get("text"),
        "detail": detail,
        "where": where,
    }


def _change_line(row: dict) -> str:
    """One line per change, in `recall`'s bracket shape so the id and the
    trust tag sit where a reader of the sibling verbs already looks."""
    marker = _MARKERS.get(row["change"], "-")
    text = row["text"] or "(content unavailable)"
    detail = f" ({row['detail']})" if row["detail"] else ""
    where = f" in {row['where']}" if row["where"] else ""
    return (f"{marker} {row['change']:<10} [{row['item_id']}] "
            f"[{row['trust'] or 'untagged'}] [{row['kind']}] {text}"
            f"{detail}{where}")


def _chain_line(retained: int, history: int, oldest: str) -> str:
    """The retention caveat, stated as reach rather than as loss.

    Pointer-derived attribution EXPIRES after `history` writes (store's own
    note at the rotation site), so a full chain cannot answer for anything
    before its oldest generation. Saying "older checkpoints have rotated out"
    as a fact would over-claim after exactly `history` writes, when nothing
    has been lost yet; saying what this chain cannot reach is true either
    way."""
    return (f"Chain: {retained} retained "
            f"(DAIMON_CHECKPOINT_HISTORY={history}) — anything before "
            f"{oldest} is not in this chain and cannot be read here.")


def _refuse(message: str, rc: int, as_json: bool) -> int:
    """One refusal boundary for both verbs. A machine caller must never have
    to parse stderr to learn the verb said no (`bucket migrate`'s contract)."""
    if as_json:
        print(json.dumps({"refused": message}, indent=2, ensure_ascii=False))
    else:
        print(f"error: {message}", file=sys.stderr)
    return rc


def _pick(entries: list[dict], args) -> tuple:
    """(older_entry, newer_entry, skipped, rc, message).

    Default is the latest readable PAIR: a torn generation between them is
    skipped and reported, because the next readable one still answers the
    question the caller asked. An explicitly named generation is never
    substituted — the caller addressed that checkpoint, so a torn one is a
    refusal, not a silent walk to its neighbour."""
    by_index = {e["index"]: e for e in entries}
    skipped = [{"index": e["index"], "pointer": e["pointer"],
                "reason": "unreadable"}
               for e in entries if e["checkpoint"] is None]
    if args.frm is None and args.to is None:
        readable = [e for e in entries if e["checkpoint"] is not None]
        if len(readable) < 2:
            return None, None, skipped, 0, None
        return readable[1], readable[0], skipped, 0, None
    frm = 1 if args.frm is None else args.frm
    to = 0 if args.to is None else args.to
    if frm <= to:
        return None, None, skipped, 2, (
            "--from must be older than --to: generation 0 is the latest "
            "checkpoint and a larger number is further back")
    for index in (frm, to):
        entry = by_index.get(index)
        if entry is None:
            return None, None, skipped, 2, (
                f"no checkpoint {index} generations back in this project "
                f"(retained: {len(entries)})")
        if entry["checkpoint"] is None:
            return None, None, skipped, 1, (
                f"{entry['pointer']} is unreadable, so generation {index} "
                "cannot be compared")
    return by_index[frm], by_index[to], skipped, 0, None


@_cli.guarded
def _cmd_diff(args) -> int:
    """What changed between two retained checkpoints of this project (#975).

    Read-only, and deliberately so. Rollback is a NON-GOAL and was settled in
    #975: a checkpoint is a record of what a session did, so restoring an
    earlier one would be a person asserting a different past. `forget`,
    `resolve` and `reverify` already say that on the record without erasing
    it. There is no `--restore`, and there will not be one.

    One snapshot judges both generations and the lifecycle reasons; nothing
    here reads a pointer itself.

    rc 0 answered (a chain too thin to compare is an answer, not an error),
    1 nothing readable to compare, 2 a refused address."""
    _cli._note_usage("diff")
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    snap = view.snapshot(project)
    entries = [_pointer_entry(p) for p in view.pointers(project, snap=snap)]
    older, newer, skipped, prc, message = _pick(entries, args)
    if message is not None:
        return _refuse(message, prc, args.json)
    readable = [e for e in entries if e["checkpoint"] is not None]
    if not readable:
        return _refuse(
            "no checkpoints in this project yet — nothing to compare",
            1, args.json)
    depth = config.checkpoint_history()
    rows = ([] if older is None
            else changes(older["checkpoint"], newer["checkpoint"], snap))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_slug": store.project_slug(project),
        "history": depth,
        "retained": len(entries),
        "truncated": len(entries) >= depth,
        "from": _endpoint(older),
        "to": _endpoint(newer),
        "skipped": skipped,
        "changes": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    render.render_history_lines(_diff_lines(payload, entries[-1]["pointer"]))
    return 0


def _diff_lines(payload: dict, oldest: str) -> list[str]:
    lines = [f"Project: {payload['project_slug']}"]
    if payload["truncated"]:
        lines.append(_chain_line(payload["retained"], payload["history"],
                                 oldest))
    for row in payload["skipped"]:
        lines.append(f"skipped {row['pointer']} ({row['reason']})")
    if payload["from"] is None:
        lines.append("only one readable checkpoint in this chain — "
                     "nothing to compare yet")
        return lines
    for label, end in (("From", payload["from"]), ("To  ", payload["to"])):
        lines.append(f"{label}: {end['pointer']} "
                     f"({end['session_id']}, {end['created']})")
    if not payload["changes"]:
        lines.append("no changes between these two checkpoints")
        return lines
    lines.extend(_change_line(row) for row in payload["changes"])
    return lines


def _origin(item: dict, retained_sessions) -> dict:
    """Who FIRST stated this, from the write-time binding (#268) and nowhere
    else. An absent binding reads as unknown rather than as the session the
    item happens to sit in: substituting a carrier for an author is the
    manufactured-corroboration failure the binding exists to prevent.

    `retained` says whether the chain still holds that session. False is not
    a defect — pointer-derived attribution EXPIRES after `history` writes —
    but it must be visible, or a reader takes an unreachable origin for a
    contradiction."""
    session_id = item.get("origin_session") or None
    return {
        "session_id": session_id,
        "author": item.get("origin_author") or None,
        "first_seen": item.get("first_seen") or None,
        "retained": bool(session_id) and session_id in retained_sessions,
    }


def _appearance_row(appearance: view.Appearance) -> dict:
    """One generation of a lineage. `carried_from` is the carry label, and
    two things about it are easy to get wrong. It is stamped with
    `setdefault`, so it names the session an item was FIRST copied from and is
    never re-stamped on later hops. And carry's twin path never stamps it at
    all (scar 0077): a session restating a carried claim in its own words
    wrote those words, so the copy label would misname the author. Its
    absence therefore means "this session wrote this wording", which is
    exactly what the line says — never "this is where the claim began". That
    question is the `Origin:` line's, and only the bound `origin_session` can
    answer it. A withheld generation says neither: it names no trust and is
    never "stated here" (scars 0086, 0077)."""
    verdict = appearance.verdict
    held = isinstance(verdict, view.Withheld)
    return {
        "index": appearance.index,
        "pointer": appearance.pointer_file,
        "session_id": appearance.session_id,
        "created": appearance.created,
        "trust": appearance.trust,
        "carried_from": appearance.carried_from,
        "native": None if held else not appearance.carried_from,
        "withheld": display.withheld_json(verdict) if held else None,
    }


@_cli.guarded
def _cmd_blame(args) -> int:
    """How one item got here: origin, every carry, every state change (#975).

    `why` answers "where did this come from" — the evidence axes behind one
    claim. This answers "how did it get here" — the sequence.

    Read-only, and rollback is the same non-goal it is for `diff`: this verb
    shows how a state was reached and offers no way to put an earlier one
    back. A forgotten or quarantined item prints its marker and never its
    text; the lifecycle is what survives deletion, the value is not. This is
    the exact-id history verb that may say `[withheld: forgotten]` for one id.

    rc 0 answered, 1 this project's chain and ledger hold no such item,
    2 a malformed id or a refused address."""
    _cli._note_usage("blame")
    if not inspector.valid_item_id(args.item_id):
        return _refuse(
            "invalid item id — expected [a-z]-[0-9a-f]{6,40}(-N)?", 2,
            args.json)
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    lineage = view.lineage(project, args.item_id)
    verdict = lineage.verdict
    if (isinstance(verdict, view.Absent) and not lineage.appearances
            and not lineage.events):
        return _refuse(
            f"no item {args.item_id!r} in this project's retained chain", 1,
            args.json)
    # The verdict answers for the item's current text, trust and binding. An
    # id with no copy at all is one the ledger still names, and has no content
    # by construction, so nothing is substituted for it.
    if isinstance(verdict, view.Found):
        item, kind = verdict.item, verdict.field.kind
        text = item.get("text") or None
        trust = item.get("trust")
    elif isinstance(verdict, view.Withheld):
        item, kind, trust = {}, verdict.kind, None
        text = display.withheld_json(verdict)
    else:
        item, kind, trust, text = {}, "unknown", None, None
    depth = config.checkpoint_history()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_slug": store.project_slug(project),
        "item_id": args.item_id,
        "kind": kind,
        "trust": trust,
        "text": text,
        "lifecycle": lineage.lifecycle,
        "origin": _origin(item, lineage.sessions),
        "history": depth,
        "retained": len(lineage.refs),
        "truncated": len(lineage.refs) >= depth,
        # oldest generation first, so a reader walks the lineage forwards
        "appearances": [_appearance_row(a)
                        for a in reversed(lineage.appearances)],
        "events": [{"ts": evt.ts, "kind": evt.kind, "status": evt.status,
                    "source": evt.source, "note": evt.note}
                   for evt in lineage.events],
        "skipped": [{"index": view._pointer_index(ref),
                     "pointer": f"{ref}.json", "reason": "unreadable"}
                    for ref in lineage.unreadable],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    oldest = f"{lineage.refs[-1]}.json" if lineage.refs else ""
    render.render_history_lines(_blame_lines(payload, oldest))
    return 0


def _origin_line(origin: dict) -> str:
    if not origin["session_id"]:
        return "Origin: origin not recorded"
    if not origin["retained"]:
        return (f"Origin: {origin['session_id']} — origin beyond retained "
                "history; this chain no longer holds that checkpoint")
    author = f" ({origin['author']})" if origin["author"] else ""
    seen = (f", first seen {origin['first_seen']}"
            if origin["first_seen"] else "")
    return f"Origin: first stated by {origin['session_id']}{author}{seen}"


def _marker_of(kind: str, held: dict) -> str:
    """The marker line for the `{"state": "withheld", ...}` object a payload
    publishes in place of a value."""
    reason = held.get("reason")
    return display.withheld_marker(view.Withheld(
        None, kind, reason if reason in ("quarantine", "closed")
        else "forgotten", held.get("quarantine_id"), ""))


def _item_line(payload: dict) -> str:
    text = payload["text"]
    if isinstance(text, dict):
        marker = _marker_of(payload["kind"], text)
        return f"Item: [{payload['item_id']}] [{payload['kind']}] {marker}"
    return (f"Item: [{payload['item_id']}] "
            f"[{payload['trust'] or 'untagged'}] [{payload['kind']}] "
            f"{text or '(content unavailable)'}")


def _blame_lines(payload: dict, oldest: str) -> list[str]:
    lines = [f"Project: {payload['project_slug']}"]
    if payload["truncated"]:
        lines.append(_chain_line(payload["retained"], payload["history"],
                                 oldest))
    for row in payload["skipped"]:
        lines.append(f"skipped {row['pointer']} ({row['reason']})")
    lines.append(_item_line(payload))
    lines.append(_origin_line(payload["origin"]))
    lines.append(f"Lifecycle: {payload['lifecycle']}")
    lines.append("Lineage:")
    if not payload["appearances"]:
        lines.append("  no retained checkpoint holds this item")
    for row in payload["appearances"]:
        if row["withheld"] is not None:
            tag = _marker_of(payload["kind"], row["withheld"])
        else:
            how = ("stated here" if row["native"]
                   else f"carried from {row['carried_from']}")
            tag = f"[{row['trust'] or 'untagged'}] {how}"
        lines.append(f"  {row['pointer']} ({row['session_id']}, "
                     f"{row['created']}) {tag}")
    lines.append("Events:")
    if not payload["events"]:
        lines.append("  none recorded")
    for evt in payload["events"]:
        note = f" — {evt['note']}" if evt["note"] else ""
        lines.append(f"  {evt['ts']} {evt['status']} "
                     f"(via {evt['source'] or 'unknown'}){note}")
    return lines


def register(sub, fmt) -> None:
    """Register the chain-reading verbs on the top-level subparsers."""
    p_diff = sub.add_parser(
        "diff",
        help="what changed between two retained checkpoints of this project "
             "(#975) — added, restated, re-tagged, and every item the chain "
             "stopped carrying, with the reason the record gives",
        description="Compare two retained checkpoints of one project and "
                    "report every item that changed. Read-only: it stamps "
                    "nothing. Rollback is a non-goal — a checkpoint records "
                    "what a session did, so restoring one would assert a "
                    "different past; use forget, resolve or reverify to say "
                    "so on the record instead.",
        epilog="Examples:\n"
               "  daimon diff\n"
               "  daimon diff --from 2 --to 0\n"
               "  daimon diff --json\n",
    )
    p_diff.add_argument(
        "--from", dest="frm", type=int, metavar="N",
        help="the OLDER endpoint, in generations back: 0 is latest.json, "
             "1 is prev-1.json (default: the latest readable pair)")
    p_diff.add_argument(
        "--to", dest="to", type=int, metavar="M",
        help="the NEWER endpoint, same numbering; must be smaller than --from")
    p_diff.add_argument(
        "--json", action="store_true", help="machine-readable change rows")
    p_diff.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)")
    p_diff.add_argument(
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`)")
    p_diff.set_defaults(func=_cli._cmd_diff)

    p_blame = sub.add_parser(
        "blame",
        help="how one item got here (#975) — the session that first stated "
             "it, every carry since, and every event that changed its state, "
             "in order",
        description="Trace one item through this project's retained "
                    "checkpoint chain. `daimon why` answers where a claim "
                    "came from; this answers how it got here. Read-only, and "
                    "there is no restore: a forgotten item keeps its "
                    "tombstone here and never its text.",
        epilog="Examples:\n"
               "  daimon blame o-3f8a2c\n"
               "  daimon blame o-3f8a2c --json\n",
    )
    p_blame.add_argument(
        "item_id", help="exact item id shown by `daimon recall`, "
                        "`daimon loops` or `daimon diff`")
    p_blame.add_argument(
        "--json", action="store_true", help="machine-readable lineage")
    p_blame.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)")
    p_blame.add_argument(
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`)")
    p_blame.set_defaults(func=_cli._cmd_blame)
