"""Amendment ledger — evidence-carrying state transitions on briefed items (#691).

An amendment says a briefed item's state advanced while the item stays open:
the issue got approved, the blocker cleared, the scope changed. It is not a
resolution (the loop is still open) and not a reverify (nothing is stale); it
is the verb between them.

Amendments are not checkpoint items and never ride events.jsonl. That store
has no channel column (a human verdict and an agent proposal would be
byte-identical on disk), folds latest-wins per ref (a confirmation would
overwrite the amendment it confirms), and deletes by append-tombstone (prose
written there outlives every deletion the user can ask for — the #419 class).
This module follows refutations.py instead: its own append-only stream, an
observed channel recorded on every row, a deterministic full-pass fold, and
rewrite deletion that reaches the bytes.

The write path is zero-LLM and the agent only ever proposes. A candidate
renders NOWHERE. It becomes render-worthy only through the session-end
byte-check (mechanical channel, quote verified against the transcript,
speaker role recorded) or a human verdict. Verification certifies
transcription, not truth — the render side labels the role for exactly that
reason.

No field may carry the target item's text: the ledger names items by id only,
and its own prose is limited to the evidence quote and a human-channel note,
both length-capped, both reachable by forget by value.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from . import config, normalize, policy, redact, store
# One channel doctrine for every ledger: authority is a property of the WRITE
# PATH, never a caller's claim about itself (see the refutations table for the
# full argument). Importing the table keeps a future channel tier ("ui",
# "signed") consistent across ledgers instead of forking per module.
from .refutations import CHANNEL_AUTHORITY, CHANNEL_LABEL

VERSION = 1
EVENTS = frozenset({"proposed", "verified", "ratified", "rejected", "retracted"})
STATES = frozenset({"candidate", "verified", "ratified", "rejected", "retracted"})
# What an amendment may say about the item, closed at the write boundary so
# nothing free-form ever rides an agent-writable path into the briefing
# (the worldcheck._KNOWN_STATES posture, applied at the ledger).
CHANGES = frozenset({"progressed", "blocked", "changed"})
# The states the briefing may render. Candidates are invisible by design:
# an unverified annotation inline in the trusted panel would let an agent
# assert state without verification.
RENDER_STATES = frozenset({"verified", "ratified"})
_EVENT_RANK = {
    # Same-order ambiguity fails toward the human verdict applying last:
    # a same-instant reject beats a mechanical verify.
    "proposed": 0,
    "verified": 1,
    "ratified": 2,
    "rejected": 3,
    "retracted": 4,
}
_AMEND_ID_RE = re.compile(r"a-[0-9a-f]{12}")
# Everything policy.stamp_item_ids can mint (relations' target contract):
# prefix letter per list key, width ladder plus the legacy 6-hex era, and
# the `-{n}` identical-text twin counter.
_ITEM_ID_RE = re.compile(r"[orsuc]-[0-9a-f]{6,40}(?:-\d+)?")
_SPACE_RE = re.compile(r"\s+")
_MAX_TEXT = 2000

# Every field of a ledger row that can hold plaintext (#645 discipline).
# One declaration, two consumers: `forget_content_key` decides which records
# a deletion reaches, and `privacy.audit_project` decides which fields it
# hashes when proving the deletion happened.
_PLAINTEXT_FIELDS = ("evidence", "note")


class AmendmentError(ValueError):
    """A requested ledger transition is invalid or cannot be persisted."""


def _path(project_dir=None):
    slug = store.project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "amendments.jsonl"


def _text(name: str, value, *, required: bool = True) -> str:
    out = _SPACE_RE.sub(" ", str(value or "")).strip()
    if required and not out:
        raise AmendmentError(f"{name} is required")
    if len(out) > _MAX_TEXT:
        raise AmendmentError(
            f"{name} is too long ({len(out)} > {_MAX_TEXT} characters)")
    return out


def make_id(item_id: str, change: str, evidence: str) -> str:
    """Stable logical id from the redacted target+change+evidence identity."""
    raw = f"{item_id}\0{change}\0{evidence.casefold()}"
    return "a-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _stamp(event: str, amendment_id: str, channel: str) -> dict:
    if event not in EVENTS:
        raise AmendmentError(f"unknown amendment event: {event}")
    if not _AMEND_ID_RE.fullmatch(str(amendment_id or "")):
        raise AmendmentError(f"invalid amendment id: {amendment_id!r}")
    if channel not in CHANNEL_AUTHORITY:
        raise AmendmentError(
            f"channel must be one of: {', '.join(sorted(CHANNEL_AUTHORITY))}")
    # Derived, never accepted: no way to name one channel and claim another's
    # authority.
    authority = CHANNEL_AUTHORITY[channel]
    order = time.time_ns()
    ts = datetime.fromtimestamp(order / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return {
        "version": VERSION,
        "ts": ts,
        "order": order,
        "event_id": uuid.uuid4().hex,
        "event": event,
        "amendment_id": amendment_id,
        "channel": channel,
        "authority": authority,
        "author": config.author(),
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


def append(row: dict, project_dir=None) -> bool:
    """Append one admitted lifecycle row. Never mutates another ledger."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    admitted = policy.admit_row(row, redact_fields=("evidence", "note", "author"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            if _is_torn(path):
                handle.write("\n")
            handle.write(json.dumps(admitted, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def events(project_dir=None) -> list[dict]:
    """Read valid ledger rows best-effort; malformed lines never sink reads."""
    path = _path(project_dir)
    if path is None or not path.exists():
        return []
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (not isinstance(row, dict)
                or row.get("event") not in EVENTS
                or not _AMEND_ID_RE.fullmatch(str(row.get("amendment_id") or ""))):
            continue
        copy = dict(row)
        copy["_line"] = index
        rows.append(copy)
    return rows


def fold(rows: list[dict]) -> dict[str, dict]:
    """Fold lifecycle facts into current records, deterministic under reorder.

    Full-pass, never latest-wins: amendment history is witness data (the
    corroborations() lesson) and a later event must not erase the record of
    an earlier one — the stream keeps every row; the fold states what holds
    NOW.
    """
    def _integer(row, key, default=0):
        try:
            return int(row.get(key) or default)
        except (TypeError, ValueError):
            return default

    ordered = sorted(rows, key=lambda row: (
        _integer(row, "order"),
        _EVENT_RANK.get(str(row.get("event") or ""), 99),
        str(row.get("event_id") or ""),
        _integer(row, "_line"),
    ))
    out: dict[str, dict] = {}
    for row in ordered:
        a_id = row["amendment_id"]
        event = row["event"]
        current = out.get(a_id)
        if event == "proposed":
            if current is not None:
                continue  # duplicate logical proposal, first writer wins
            state = (
                "ratified" if row.get("ratified") is True
                and CHANNEL_AUTHORITY.get(row.get("channel")) == "human"
                else "candidate")
            out[a_id] = {
                "amendment_id": a_id,
                "state": state,
                "item_id": str(row.get("item_id") or ""),
                "change": str(row.get("change") or ""),
                "evidence": str(row.get("evidence") or ""),
                "evidence_role": None,
                "note": str(row.get("note") or ""),
                "proposed_by": row.get("authority"),
                "proposed_channel": row.get("channel"),
                "proposed_author": row.get("author"),
                "verdict_label": (CHANNEL_LABEL.get(row.get("channel"))
                                  if state == "ratified" else None),
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "history_count": 1,
            }
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert here
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        if event == "verified":
            # The one mechanical transition: the session-end byte-check found
            # the quote in the transcript. It never overrides a verdict.
            if (current["state"] == "candidate"
                    and CHANNEL_AUTHORITY.get(row.get("channel")) == "mechanical"):
                current["state"] = "verified"
                current["evidence_role"] = str(row.get("evidence_role") or "")
                current["verdict_label"] = "quote-verified"
        elif event == "ratified":
            if (current["state"] in ("candidate", "verified")
                    and CHANNEL_AUTHORITY.get(row.get("channel")) == "human"):
                current["state"] = "ratified"
                current["verdict_label"] = CHANNEL_LABEL.get(row.get("channel"))
        elif event == "rejected":
            if (current["state"] != "retracted"
                    and CHANNEL_AUTHORITY.get(row.get("channel")) == "human"):
                current["state"] = "rejected"
                current["verdict_label"] = None
                current["note"] = str(row.get("note") or current["note"])
        elif event == "retracted":
            if CHANNEL_AUTHORITY.get(row.get("channel")) == "human":
                current["state"] = "retracted"
                current["verdict_label"] = None
    return out


def records(project_dir=None) -> dict[str, dict]:
    return fold(events(project_dir=project_dir))


def get(amendment_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(amendment_id)


def renderable(project_dir=None) -> dict[str, list[dict]]:
    """Render-worthy amendments grouped by target item id.

    The one read the briefing consumes. Only RENDER_STATES survive: a
    candidate renders nowhere, a rejected or retracted amendment is history.
    Sorted oldest-first per item so multiple amendments read as a timeline.
    """
    by_item: dict[str, list[dict]] = {}
    for record in records(project_dir=project_dir).values():
        if record["state"] not in RENDER_STATES:
            continue
        by_item.setdefault(record["item_id"], []).append(record)
    for rows in by_item.values():
        rows.sort(key=lambda row: (row.get("created_at") or "",
                                   row["amendment_id"]))
    return by_item


def propose(*, item_id: str, change: str, evidence: str, channel: str,
            note: str = "", project_dir=None) -> str:
    """Open an amendment. Any channel may propose; only a human channel may
    carry a note or land already-ratified."""
    if not _ITEM_ID_RE.fullmatch(str(item_id or "")):
        raise AmendmentError(f"invalid item id: {item_id!r}")
    if change not in CHANGES:
        raise AmendmentError(
            f"change must be one of: {', '.join(sorted(CHANGES))}")
    evidence = _text("evidence", evidence)
    note = _text("note", note, required=False)
    human = CHANNEL_AUTHORITY.get(channel) == "human"
    if note and not human:
        raise AmendmentError(
            "a note requires a human channel; agent amendments carry only "
            "the typed change and the evidence quote")
    # Identity derives from the bytes that can actually persist.
    evidence, _ = redact.redact_text(evidence)
    a_id = make_id(item_id, change, evidence)
    if get(a_id, project_dir=project_dir) is not None:
        raise AmendmentError(
            f"{a_id} already exists for this item, change, and evidence; "
            f"ratify or reject it instead")
    row = _stamp("proposed", a_id, channel)
    row.update({"item_id": item_id, "change": change, "evidence": evidence})
    if note:
        row["note"] = note
    if human:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise AmendmentError(
            "amendment not written (daimon disabled, project unknown, or "
            "ledger unwritable)")
    return a_id


def verify(amendment_id: str, *, role: str, project_dir=None) -> None:
    """Record the session-end byte-check outcome. Mechanical channel only —
    reachable solely from the in-process capture pass, exactly like the
    refutation ledger's `activated`. The role is the transcript speaker the
    quote was found under; the render side labels it because verification
    certifies transcription, not truth."""
    current = get(amendment_id, project_dir=project_dir)
    if current is None:
        raise AmendmentError(f"unknown amendment: {amendment_id}")
    row = _stamp("verified", amendment_id, "mechanical")
    row["evidence_role"] = _text("role", role)
    if not append(row, project_dir=project_dir):
        raise AmendmentError("verification not written")


def ratify(amendment_id: str, *, channel: str, project_dir=None) -> None:
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise AmendmentError(
            "ratification requires a human channel; this call arrived "
            f"through {channel!r}")
    current = get(amendment_id, project_dir=project_dir)
    if current is None:
        raise AmendmentError(f"unknown amendment: {amendment_id}")
    if current["state"] not in ("candidate", "verified"):
        raise AmendmentError(
            f"{amendment_id} is {current['state']}; only a candidate or "
            "verified amendment can be ratified")
    row = _stamp("ratified", amendment_id, channel)
    if not append(row, project_dir=project_dir):
        raise AmendmentError("ratification not written")


def reject(amendment_id: str, *, channel: str, note: str = "",
           project_dir=None) -> None:
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise AmendmentError(
            "rejection requires a human channel; this call arrived "
            f"through {channel!r}")
    current = get(amendment_id, project_dir=project_dir)
    if current is None:
        raise AmendmentError(f"unknown amendment: {amendment_id}")
    if current["state"] == "retracted":
        raise AmendmentError(f"{amendment_id} is already retracted")
    row = _stamp("rejected", amendment_id, channel)
    row["note"] = _text("note", note, required=False)
    if not append(row, project_dir=project_dir):
        raise AmendmentError("rejection not written")


def row_content_keys(row: dict) -> set[str]:
    """Canonical keys for every plaintext field this row carries (#645).

    The one reader of _PLAINTEXT_FIELDS, so the deleter below and
    `privacy.audit_project` cannot drift apart about what counts as
    plaintext on this surface."""
    out: set[str] = set()
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.add(normalize.content_key(value))
    return out


def _rewrite_without(doomed: set[str], project_dir=None) -> list[str]:
    """Rewrite the ledger dropping every row of the doomed record ids.

    Raw LINES, never `events()` output — that reader is tolerant and would
    silently delete rows a future daimon added (the scar 0025/0042 shape:
    a forgiving read feeding a write). Atomic or nothing."""
    doomed.discard("")
    if not doomed:
        return []
    path = _path(project_dir)
    if path is None or not path.exists():
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
            continue  # torn rows are expendable; keeping one leaves bytes
        if (isinstance(row, dict)
                and str(row.get("amendment_id") or "") in doomed):
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


def forget_content_key(content_key: str, *, project_dir=None) -> list[str]:
    """Remove every record holding `content_key` in a plaintext field.

    The rewrite path exists because this ledger carries plaintext by design
    (evidence quotes, human notes), so it is in the checkpoint's deletion
    category — a removal must reach the bytes (#578 posture). Matches on the
    same canonical whole-value key the checkpoint splice uses, never on
    substring containment. Every row of a matched record goes. No kill-switch
    check: forget is the ratified deletion exemption (#421)."""
    doomed = {
        str(row.get("amendment_id") or "")
        for row in events(project_dir=project_dir)
        if content_key in row_content_keys(row)
    }
    return _rewrite_without(doomed, project_dir=project_dir)


def forget_item_id(item_id: str, *, project_dir=None) -> list[str]:
    """Remove every record targeting a forgotten item.

    An amendment is ABOUT its item; when the item is deleted, rows that name
    it are dangling references whose evidence prose may paraphrase the very
    content the user asked to remove. The relations ledger keeps its rows on
    a forgotten id because no field there can carry text; this ledger's rows
    do, so they go with the item."""
    doomed = {
        str(row.get("amendment_id") or "")
        for row in events(project_dir=project_dir)
        if str(row.get("item_id") or "") == str(item_id or "")
    }
    return _rewrite_without(doomed, project_dir=project_dir)
